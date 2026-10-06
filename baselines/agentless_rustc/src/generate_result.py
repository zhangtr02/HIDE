from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .config import load_config
from .io_utils import load_json, read_rustcbugs_csv, unique_keep_order, write_json


@dataclass(frozen=True)
class AccuracyStat:
    hit: int
    total: int
    percentage: str


@dataclass(frozen=True)
class BugResult:
    bug_id: str
    top1_hit: int
    top3_hit: int
    top5_hit: int
    top10_hit: int
    method_top1_hit: int
    method_top3_hit: int
    method_top5_hit: int
    method_top10_hit: int
    groundtruth_position: int | None
    method_groundtruth_rank: int | None
    failure_type: str
    method_failure_type: str
    top10: List[str]
    method_top5: List[str]
    method_top10: List[str]
    groundtruth_files: List[str]
    groundtruth_methods: List[Dict[str, Any]]
    wall_time_sec: float
    llm_request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    method_wall_time_sec: float
    method_llm_request_count: int
    method_input_tokens: int
    method_output_tokens: int
    method_total_tokens: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Agentless-rustc result.json")
    parser.add_argument("--bug-id", default="", help="Evaluate one bug id")
    parser.add_argument("--bug-ids", default="", help="Evaluate comma-separated bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate first N rows from rustcbugs.csv")
    parser.add_argument("--strict", action="store_true", help="Fail if any report or groundtruth file is missing")
    parser.add_argument("--output", default="", help="Output path, default is agentless_rustc/result.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 0:
        raise RuntimeError("--limit must be >= 0")
    cfg = load_config()
    bug_ids = select_bug_ids(args, read_rustcbugs_csv(cfg.paths.csv_file))
    results: List[BugResult] = []
    skipped: List[Dict[str, str]] = []

    for bug_id in bug_ids:
        report_path = cfg.paths.report_dir / f"{bug_id}.json"
        gt_path = cfg.paths.groundtruth_dir / f"{bug_id}.json"
        if not report_path.exists():
            reason = "missing_report"
            if args.strict:
                raise FileNotFoundError(f"{reason}: {bug_id}")
            skipped.append({"bug_id": bug_id, "reason": reason})
            continue
        report = load_json(report_path)
        if not gt_path.exists():
            reason = "missing_groundtruth"
            if args.strict:
                raise FileNotFoundError(f"{reason}: {bug_id}")
            skipped.append({"bug_id": bug_id, "reason": reason})
            continue
        groundtruth = load_groundtruth(gt_path)
        method_groundtruth = load_method_groundtruth(cfg.paths.method_groundtruth_dir / f"{bug_id}.json")
        results.append(
            build_bug_result(
                report=report,
                bug_id=bug_id,
                groundtruth_files=groundtruth,
                method_groundtruth=method_groundtruth,
            )
        )

    output_obj: Dict[str, Any] = {
        "evaluated_bug_count": len(results),
        "skipped_bug_count": len(skipped),
        "top1_accuracy": asdict(build_accuracy(results, "top1_hit")),
        "top3_accuracy": asdict(build_accuracy(results, "top3_hit")),
        "top5_accuracy": asdict(build_accuracy(results, "top5_hit")),
        "top10_accuracy": asdict(build_accuracy(results, "top10_hit")),
        "method_top1_accuracy": asdict(build_accuracy(results, "method_top1_hit")),
        "method_top3_accuracy": asdict(build_accuracy(results, "method_top3_hit")),
        "method_top5_accuracy": asdict(build_accuracy(results, "method_top5_hit")),
        "method_top10_accuracy": asdict(build_accuracy(results, "method_top10_hit")),
        "efficiency": build_efficiency(results),
        "method_efficiency": build_efficiency(results, prefix="method_"),
        "failure_type_counts": count_by(results, "failure_type"),
        "method_failure_type_counts": count_by(results, "method_failure_type"),
        "bugs": [asdict(item) for item in results],
    }
    if skipped:
        output_obj["skipped"] = skipped

    output_path = Path(args.output).resolve() if args.output else cfg.paths.root / "result.json"
    write_json(output_path, output_obj)
    print(f"[result] saved {output_path}", flush=True)


def select_bug_ids(args: argparse.Namespace, rows: List[Tuple[str, str, str]]) -> List[str]:
    csv_bug_ids = unique_keep_order([bug_id for bug_id, _toolchain, _build_args in rows])
    requested: List[str] = []
    if args.bug_id.strip():
        requested.append(args.bug_id.strip())
    if args.bug_ids.strip():
        requested.extend(item.strip() for item in args.bug_ids.split(",") if item.strip())
    requested = unique_keep_order(requested)
    if requested:
        missing = [bug_id for bug_id in requested if bug_id not in set(csv_bug_ids)]
        if missing:
            raise RuntimeError(f"bug_id not found in rustcbugs.csv: {', '.join(missing)}")
        return requested
    return csv_bug_ids[: args.limit] if args.limit else csv_bug_ids


def build_bug_result(
    *,
    report: Dict[str, Any],
    bug_id: str,
    groundtruth_files: List[str],
    method_groundtruth: List[Dict[str, Any]],
) -> BugResult:
    top10 = read_string_list(report.get("final_top10"))[:10]
    method_items = final_method_items(report)
    method_top5 = [method_identifier(item) for item in method_items[:5]]
    method_top10 = [method_identifier(item) for item in method_items[:10]]
    position = first_position(top10, groundtruth_files)
    method_rank = first_method_rank(method_items[:10], method_groundtruth)
    eff, method_eff = read_efficiency_parts(report)
    return BugResult(
        bug_id=bug_id,
        top1_hit=int(position == 1),
        top3_hit=int(position is not None and position <= 3),
        top5_hit=int(position is not None and position <= 5),
        top10_hit=int(position is not None and position <= 10),
        method_top1_hit=int(method_rank == 1),
        method_top3_hit=int(method_rank is not None and method_rank <= 3),
        method_top5_hit=int(method_rank is not None and method_rank <= 5),
        method_top10_hit=int(method_rank is not None and method_rank <= 10),
        groundtruth_position=position,
        method_groundtruth_rank=method_rank,
        failure_type="top10_hit" if position is not None and position <= 10 else "file_top10_miss",
        method_failure_type=infer_method_failure_type(method_rank, method_groundtruth, method_items),
        top10=top10,
        method_top5=method_top5,
        method_top10=method_top10,
        groundtruth_files=groundtruth_files,
        groundtruth_methods=method_groundtruth,
        wall_time_sec=eff["wall_time_sec"],
        llm_request_count=eff["llm_request_count"],
        input_tokens=eff["input_tokens"],
        output_tokens=eff["output_tokens"],
        total_tokens=eff["total_tokens"],
        method_wall_time_sec=method_eff["wall_time_sec"],
        method_llm_request_count=method_eff["llm_request_count"],
        method_input_tokens=method_eff["input_tokens"],
        method_output_tokens=method_eff["output_tokens"],
        method_total_tokens=method_eff["total_tokens"],
    )


def final_method_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    wanted = read_string_list(report.get("final_method_top10"))
    final_items = report.get("final_method_items")
    final_group: List[Dict[str, Any]] = []
    if isinstance(final_items, list):
        final_group = [item for item in final_items if isinstance(item, dict)]

    extra_groups: List[List[Dict[str, Any]]] = []
    for section, key in [("line_level", "mapped_methods"), ("related_level", "matched_methods")]:
        obj = report.get(section) if isinstance(report.get(section), dict) else {}
        group: List[Dict[str, Any]] = []
        for item in obj.get(key, []) if isinstance(obj.get(key), list) else []:
            if isinstance(item, dict) and item.get("id"):
                group.append(item)
        extra_groups.append(group)
    if final_group or any(extra_groups):
        return merge_method_items(final_group, *extra_groups)
    return [{"id": method_id} for method_id in wanted]


def merge_method_items(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            method_id = method_identifier(item)
            if not method_id or method_id in seen:
                continue
            seen.add(method_id)
            out.append(item)
    return out


def method_identifier(item: Dict[str, Any]) -> str:
    method_id = str(item.get("id") or "").strip()
    if method_id:
        return method_id
    file_id = str(item.get("file") or "").strip()
    qualified_name = str(item.get("qualified_name") or item.get("item_name") or "").strip()
    return f"{file_id}::{qualified_name}@{item.get('start_line')}-{item.get('end_line')}"


def load_groundtruth(path: Path) -> List[str]:
    obj = load_json(path)
    files = read_string_list(obj.get("compiler_rs_files") or obj.get("compiler_files") or obj.get("all_files"))
    return [item for item in files if item.startswith("compiler/") and item.endswith(".rs")]


def load_method_groundtruth(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    obj = load_json(path)
    methods = obj.get("methods") if isinstance(obj.get("methods"), list) else []
    return [item for item in methods if isinstance(item, dict)]


def infer_method_failure_type(method_rank: int | None, method_groundtruth: List[Dict[str, Any]], method_items: List[Dict[str, Any]]) -> str:
    if not method_groundtruth:
        return "missing_method_groundtruth"
    if method_rank is not None and method_rank <= 5:
        return "method_top5_hit"
    if method_rank is not None and method_rank <= 10:
        return "method_top10_hit"
    if not method_items:
        return "no_method_candidates"
    return "method_top5_miss"


def first_position(predicted: List[str], expected: Iterable[str]) -> int | None:
    expected_set = {item for item in expected if item}
    for index, item in enumerate(predicted, start=1):
        if item in expected_set:
            return index
    return None


def first_method_rank(predicted: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> int | None:
    if not expected:
        return None
    for index, item in enumerate(predicted, start=1):
        if any(method_matches(item, exp) for exp in expected):
            return index
    return None


def method_matches(predicted: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    predicted_id = str(predicted.get("id") or "")
    expected_id = str(expected.get("id") or "")
    if predicted_id and expected_id and predicted_id == expected_id:
        return True
    if str(predicted.get("file") or "") != str(expected.get("file") or ""):
        return False
    predicted_name = normalize_method_name(predicted.get("qualified_name") or predicted.get("item_name"))
    expected_name = normalize_method_name(expected.get("qualified_name") or expected.get("item_name"))
    if predicted_name and expected_name and predicted_name == expected_name:
        return True
    start = int_value(predicted.get("start_line"))
    end = int_value(predicted.get("end_line"))
    exp_start = int_value(expected.get("start_line"))
    exp_end = int_value(expected.get("end_line"))
    return bool(start and end and exp_start and exp_end and start <= exp_end and exp_start <= end)


def read_efficiency_parts(report: Dict[str, Any]) -> tuple[Dict[str, int | float], Dict[str, int | float]]:
    efficiency = report.get("efficiency") if isinstance(report.get("efficiency"), dict) else {}
    usage = efficiency.get("llm_usage") if isinstance(efficiency.get("llm_usage"), dict) else {}
    stage_times = efficiency.get("stage_times_sec") if isinstance(efficiency.get("stage_times_sec"), dict) else {}
    method_stages = {"load_method_candidates", "related_level_llm", "line_level_llm"}
    method_wall_time = sum(float_value(value) for stage, value in stage_times.items() if stage in method_stages)
    total_wall_time = float_value(efficiency.get("total_wall_time_sec"))
    requests = [item for item in usage.get("requests", []) if isinstance(item, dict)]
    if not requests:
        file_usage = aggregate_usage(usage)
        method_usage = aggregate_usage({})
    else:
        is_method_request = lambda item: str(item.get("stage") or "").startswith(("related_level", "line_level"))
        file_usage = aggregate_usage([item for item in requests if not is_method_request(item)])
        method_usage = aggregate_usage([item for item in requests if is_method_request(item)])
    return (
        {"wall_time_sec": max(0.0, total_wall_time - method_wall_time), **file_usage},
        {"wall_time_sec": method_wall_time, **method_usage},
    )


def aggregate_usage(source: Any) -> Dict[str, int]:
    if isinstance(source, dict):
        return {
            "llm_request_count": int_value(source.get("request_count")),
            "input_tokens": int_value(source.get("input_tokens")),
            "output_tokens": int_value(source.get("output_tokens")),
            "total_tokens": int_value(source.get("total_tokens")),
        }
    requests = [item for item in source if isinstance(item, dict)] if isinstance(source, list) else []
    return {
        "llm_request_count": len(requests),
        "input_tokens": sum(int_value(item.get("input_tokens")) for item in requests),
        "output_tokens": sum(int_value(item.get("output_tokens")) for item in requests),
        "total_tokens": sum(
            int_value(item.get("total_tokens")) or int_value(item.get("input_tokens")) + int_value(item.get("output_tokens"))
            for item in requests
        ),
    }


def build_accuracy(results: List[BugResult], field: str) -> AccuracyStat:
    total = len(results)
    hit = sum(int(getattr(item, field)) for item in results)
    return AccuracyStat(hit=hit, total=total, percentage=percentage(hit, total))


def build_efficiency(results: List[BugResult], prefix: str = "") -> Dict[str, Any]:
    total = len(results)
    wall = sum(float(getattr(item, f"{prefix}wall_time_sec")) for item in results)
    req = sum(int(getattr(item, f"{prefix}llm_request_count")) for item in results)
    input_tokens = sum(int(getattr(item, f"{prefix}input_tokens")) for item in results)
    output_tokens = sum(int(getattr(item, f"{prefix}output_tokens")) for item in results)
    total_tokens = sum(int(getattr(item, f"{prefix}total_tokens")) for item in results)
    return {
        "total_wall_time_sec": round(wall, 3),
        "average_wall_time_sec": round(wall / total, 3) if total else 0,
        "total_llm_requests": req,
        "average_llm_requests": round(req / total, 3) if total else 0,
        "total_input_tokens": input_tokens,
        "average_input_tokens": round(input_tokens / total, 3) if total else 0,
        "total_output_tokens": output_tokens,
        "average_output_tokens": round(output_tokens / total, 3) if total else 0,
        "total_tokens": total_tokens,
        "average_total_tokens": round(total_tokens / total, 3) if total else 0,
    }


def count_by(results: List[BugResult], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in results:
        value = str(getattr(item, field))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def read_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item or "").strip() for item in value if str(item or "").strip()]


def normalize_method_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def percentage(hit: int, total: int) -> str:
    return "0.00%" if total == 0 else f"{hit / total * 100:.2f}%"


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def float_value(value: Any) -> float:
    try:
        return round(float(value or 0), 3)
    except Exception:
        return 0.0


if __name__ == "__main__":
    main()
