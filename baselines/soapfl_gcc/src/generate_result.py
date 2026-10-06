from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .config import load_config
from .io_utils import load_json, load_json_if_exists, read_gccbugs_csv, unique_keep_order, write_json


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
    groundtruth_modules: List[str]
    groundtruth_methods: List[Dict[str, Any]]
    wall_time_sec: float
    llm_request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate SoapFL-GCC method-level result.json")
    parser.add_argument("--bug-id", default="", help="Evaluate one GCC bug instance id")
    parser.add_argument("--bug-ids", default="", help="Evaluate comma-separated GCC bug instance ids")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate first N rows from dataset CSV")
    parser.add_argument("--strict", action="store_true", help="Fail if any report or groundtruth file is missing")
    parser.add_argument("--output", default="", help="Output path, default is soapfl_gcc/result.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 0:
        raise RuntimeError("--limit must be >= 0")
    cfg = load_config()
    output_path = Path(args.output).resolve() if args.output else cfg.paths.root / "result.json"

    bug_ids = select_bug_ids(args, read_gccbugs_csv(cfg.paths.csv_file))
    results: List[BugResult] = []
    skipped: List[Dict[str, str]] = []

    for bug_id in bug_ids:
        report_path = cfg.paths.report_dir / f"{bug_id}.json"
        groundtruth_path = cfg.paths.groundtruth_dir / f"{bug_id}.json"
        method_groundtruth_path = cfg.paths.method_groundtruth_dir / f"{bug_id}.json"
        missing = [str(path) for path in (report_path, groundtruth_path) if not path.exists()]
        if missing:
            if args.strict:
                raise FileNotFoundError(f"missing inputs for bug_id={bug_id}: {', '.join(missing)}")
            skipped.append({"bug_id": bug_id, "missing": ", ".join(missing)})
            continue

        report = load_json(report_path)
        groundtruth = load_groundtruth(groundtruth_path)
        method_groundtruth = load_method_groundtruth(method_groundtruth_path)
        results.append(
            build_bug_result(
                report=report,
                bug_id=bug_id,
                groundtruth=groundtruth,
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
        "failure_type_counts": count_by(results, "failure_type"),
        "method_failure_type_counts": count_by(results, "method_failure_type"),
        "bugs": [asdict(item) for item in results],
    }
    if skipped:
        output_obj["skipped"] = skipped

    write_json(output_path, output_obj)
    print(f"[result] saved {output_path}", flush=True)


def select_bug_ids(args: argparse.Namespace, rows: List[Dict[str, str]]) -> List[str]:
    csv_bug_ids = unique_keep_order([row.get("instance_id", "") for row in rows])
    requested: List[str] = []
    if args.bug_id.strip():
        requested.append(args.bug_id.strip())
    if args.bug_ids.strip():
        requested.extend(item.strip() for item in args.bug_ids.split(",") if item.strip())
    requested = unique_keep_order(requested)
    if requested:
        known = set(csv_bug_ids)
        missing = [bug_id for bug_id in requested if bug_id not in known]
        if missing:
            raise RuntimeError(f"bug_id not found in dataset CSV: {', '.join(missing)}")
        return requested
    return csv_bug_ids[: args.limit] if args.limit else csv_bug_ids


def build_bug_result(
    *,
    report: Dict[str, Any],
    bug_id: str,
    groundtruth: Dict[str, List[str]],
    method_groundtruth: List[Dict[str, Any]],
) -> BugResult:
    top10 = read_string_list(report.get("final_top10"))[:10]
    method_items = final_method_items(report)
    method_top5 = [method_identifier(item) for item in method_items[:5]]
    method_top10 = [method_identifier(item) for item in method_items[:10]]
    position = first_position(top10, groundtruth["files"])
    method_rank = first_method_rank(method_items[:10], method_groundtruth)
    eff = read_efficiency(report)
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
        groundtruth_files=groundtruth["files"],
        groundtruth_modules=groundtruth["modules"],
        groundtruth_methods=method_groundtruth,
        wall_time_sec=eff["wall_time_sec"],
        llm_request_count=eff["llm_request_count"],
        input_tokens=eff["input_tokens"],
        output_tokens=eff["output_tokens"],
        total_tokens=eff["total_tokens"],
    )


def load_groundtruth(path: Path) -> Dict[str, List[str]]:
    obj = load_json(path)
    files = read_string_list(obj.get("buggy_files") or obj.get("all_buggy_files"))
    modules = read_string_list(obj.get("buggy_modules")) or unique_keep_order(infer_module(file_id) for file_id in files)
    return {"files": files, "modules": modules}


def load_method_groundtruth(path: Path) -> List[Dict[str, Any]]:
    obj = load_json_if_exists(path)
    if not obj:
        return []
    methods = obj.get("methods") if isinstance(obj.get("methods"), list) else []
    return [item for item in methods if isinstance(item, dict)]


def final_method_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    final_items = report.get("final_method_items") if isinstance(report.get("final_method_items"), list) else []
    final_group = [item for item in final_items if isinstance(item, dict)]
    method_level = report.get("method_level") if isinstance(report.get("method_level"), dict) else {}
    reviewed = method_level.get("reviewed_methods") if isinstance(method_level.get("reviewed_methods"), list) else []
    reviewed_group = [item for item in reviewed if isinstance(item, dict)]
    if final_group or reviewed_group:
        return merge_method_items(final_group, reviewed_group)
    wanted = read_string_list(report.get("final_method_top10"))
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


def read_efficiency(report: Dict[str, Any]) -> Dict[str, int | float]:
    efficiency = report.get("efficiency") if isinstance(report.get("efficiency"), dict) else {}
    usage = efficiency.get("llm_usage") if isinstance(efficiency.get("llm_usage"), dict) else {}
    return {
        "wall_time_sec": float_value(efficiency.get("total_wall_time_sec")),
        "llm_request_count": int_value(usage.get("request_count")),
        "input_tokens": int_value(usage.get("input_tokens")),
        "output_tokens": int_value(usage.get("output_tokens")),
        "total_tokens": int_value(usage.get("total_tokens")),
    }


def build_accuracy(results: List[BugResult], field: str) -> AccuracyStat:
    total = len(results)
    hit = sum(int(getattr(item, field)) for item in results)
    percentage = f"{(hit / total * 100):.2f}%" if total else "0.00%"
    return AccuracyStat(hit=hit, total=total, percentage=percentage)


def build_efficiency(results: List[BugResult]) -> Dict[str, Any]:
    total = len(results)
    total_tokens = sum(item.total_tokens for item in results)
    total_input = sum(item.input_tokens for item in results)
    total_output = sum(item.output_tokens for item in results)
    total_requests = sum(item.llm_request_count for item in results)
    total_wall = sum(item.wall_time_sec for item in results)
    return {
        "total_wall_time_sec": round(total_wall, 3),
        "avg_wall_time_sec": round(total_wall / total, 3) if total else 0,
        "total_llm_requests": total_requests,
        "avg_llm_requests": round(total_requests / total, 3) if total else 0,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tokens": total_tokens,
        "avg_total_tokens": round(total_tokens / total, 3) if total else 0,
    }


def count_by(results: List[BugResult], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in results:
        key = str(getattr(item, field))
        counts[key] = counts.get(key, 0) + 1
    return counts


def read_string_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def method_identifier(item: Dict[str, Any]) -> str:
    return str(item.get("id") or item.get("qualified_name") or item.get("item_name") or "").strip()


def normalize_method_name(value: Any) -> str:
    text = str(value or "").strip()
    return " ".join(text.split())


def infer_module(file_id: str) -> str:
    parts = file_id.split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else file_id


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def float_value(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


if __name__ == "__main__":
    main()
