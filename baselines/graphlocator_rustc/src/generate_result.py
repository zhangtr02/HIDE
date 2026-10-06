from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import load_runtime_config
else:
    from .config import load_runtime_config


@dataclass(frozen=True)
class AccuracyStat:
    hit: int
    total: int
    percentage: str


@dataclass(frozen=True)
class EfficiencySummary:
    total_wall_time_sec: float
    average_wall_time_sec: float
    total_llm_requests: int
    average_llm_requests: float
    total_input_tokens: int
    average_input_tokens: float
    total_output_tokens: int
    average_output_tokens: float
    total_tokens: int
    average_total_tokens: float
    stage_time_totals_sec: Dict[str, float]
    stage_time_averages_sec: Dict[str, float]
    model_counts: Dict[str, int]


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


def status(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate GraphLocator-rustc result.json from reports")
    parser.add_argument("--bug-id", default="", help="Evaluate one bug id")
    parser.add_argument("--bug-ids", default="", help="Evaluate comma-separated bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate the first N rows from rustcbugs.csv")
    parser.add_argument("--strict", action="store_true", help="Fail if any report or groundtruth file is missing")
    parser.add_argument("--output", default="", help="Output path, default is graphlocator_rustc/result.json")
    return parser.parse_args()


def read_bug_ids(csv_path: Path) -> List[str]:
    out: List[str] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if row and (row[0] or "").strip():
                out.append((row[0] or "").strip())
    return unique_keep_order(out)


def select_bug_ids(args: argparse.Namespace, csv_bug_ids: List[str]) -> List[str]:
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
            raise RuntimeError(f"bug_id not found in rustcbugs.csv: {', '.join(missing)}")
        return requested
    return csv_bug_ids[: args.limit] if args.limit else csv_bug_ids


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def compute_bug_result(
    report: Dict[str, Any],
    groundtruth_files: List[str],
    method_groundtruth: List[Dict[str, Any]],
    bug_id: str,
) -> BugResult:
    top10 = selected_file_paths(report)[:10]
    method_items = ranked_method_items(report)
    method_top5 = [method_identifier(item) for item in method_items[:5]]
    method_top10 = [method_identifier(item) for item in method_items[:10]]
    rank = first_rank(top10, groundtruth_files)
    method_rank = first_method_rank(method_items[:10], method_groundtruth)
    efficiency, method_efficiency = read_efficiency_parts(report)
    return BugResult(
        bug_id=bug_id,
        top1_hit=int(rank == 1),
        top3_hit=int(rank is not None and rank <= 3),
        top5_hit=int(rank is not None and rank <= 5),
        top10_hit=int(rank is not None and rank <= 10),
        method_top1_hit=int(method_rank == 1),
        method_top3_hit=int(method_rank is not None and method_rank <= 3),
        method_top5_hit=int(method_rank is not None and method_rank <= 5),
        method_top10_hit=int(method_rank is not None and method_rank <= 10),
        groundtruth_position=rank,
        method_groundtruth_rank=method_rank,
        failure_type="top10_hit" if rank is not None and rank <= 10 else infer_failure_type(top10),
        method_failure_type=infer_method_failure_type(method_rank, method_groundtruth, method_items),
        top10=top10,
        method_top5=method_top5,
        method_top10=method_top10,
        groundtruth_files=groundtruth_files,
        groundtruth_methods=method_groundtruth,
        wall_time_sec=efficiency["wall_time_sec"],
        llm_request_count=efficiency["llm_request_count"],
        input_tokens=efficiency["input_tokens"],
        output_tokens=efficiency["output_tokens"],
        total_tokens=efficiency["total_tokens"],
        method_wall_time_sec=method_efficiency["wall_time_sec"],
        method_llm_request_count=method_efficiency["llm_request_count"],
        method_input_tokens=method_efficiency["input_tokens"],
        method_output_tokens=method_efficiency["output_tokens"],
        method_total_tokens=method_efficiency["total_tokens"],
    )


def selected_file_paths(report: Dict[str, Any]) -> List[str]:
    return read_string_list(report.get("final_top10"))


def ranked_method_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    final_items = report.get("final_method_items")
    final_group: List[Dict[str, Any]] = []
    if isinstance(final_items, list):
        final_group = sorted(
            [item for item in final_items if isinstance(item, dict)],
            key=lambda item: int_value(item.get("rank")) or 10**9,
        )

    method_ids = read_string_list(report.get("final_method_top10"))
    by_id = method_item_index(report)
    id_group = [by_id.get(method_id, {"id": method_id}) for method_id in method_ids] if method_ids else []
    method_level = report.get("method_level") if isinstance(report.get("method_level"), dict) else {}
    found = method_level.get("found_methods") if isinstance(method_level.get("found_methods"), list) else []
    found_group = sorted(
        [item for item in found if isinstance(item, dict)],
        key=lambda item: int_value(item.get("rank")) or 10**9,
    )
    return merge_method_items(final_group, id_group, found_group)


def method_item_index(report: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    method_level = report.get("method_level") if isinstance(report.get("method_level"), dict) else {}
    for key in ("top_methods", "found_methods"):
        items = method_level.get(key) if isinstance(method_level.get(key), list) else []
        for item in items:
            if isinstance(item, dict) and item.get("id"):
                out[str(item["id"])] = item
    return out


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
    start_line = item.get("start_line")
    end_line = item.get("end_line")
    return f"{file_id}::{qualified_name}@{start_line}-{end_line}"


def load_groundtruth_files(path: Path) -> List[str]:
    obj = load_json(path)
    if not isinstance(obj, dict):
        raise RuntimeError(f"Expected groundtruth object: {path}")
    files = read_string_list(obj.get("compiler_rs_files") or obj.get("compiler_files") or obj.get("all_files"))
    return [item for item in files if item.startswith("compiler/") and item.endswith(".rs")]


def load_method_groundtruth(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    obj = load_json(path)
    methods = obj.get("methods") if isinstance(obj.get("methods"), list) else []
    return [item for item in methods if isinstance(item, dict)]


def first_rank(predicted: List[str], expected: Iterable[str]) -> int | None:
    expected_set = {item for item in expected if item}
    for index, path in enumerate(predicted, start=1):
        if path in expected_set:
            return index
    return None


def first_method_rank(predicted: List[Dict[str, Any]], expected: List[Dict[str, Any]]) -> int | None:
    if not expected:
        return None
    for index, item in enumerate(predicted, start=1):
        if any(method_matches(item, expected_item) for expected_item in expected):
            return index
    return None


def method_matches(predicted: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    predicted_file = str(predicted.get("file") or "").strip()
    expected_file = str(expected.get("file") or "").strip()
    if not predicted_file or predicted_file != expected_file:
        return False

    predicted_id = str(predicted.get("id") or "").strip()
    expected_id = str(expected.get("id") or "").strip()
    if predicted_id and expected_id and predicted_id == expected_id:
        return True

    predicted_qualified = normalize_method_name(predicted.get("qualified_name"))
    expected_qualified = normalize_method_name(expected.get("qualified_name"))
    if predicted_qualified and expected_qualified and predicted_qualified == expected_qualified:
        return True

    predicted_name = normalize_method_name(predicted.get("item_name") or predicted.get("qualified_name"))
    expected_name = normalize_method_name(expected.get("item_name") or expected.get("qualified_name"))
    if predicted_name and expected_name and predicted_name == expected_name:
        return True

    start = int_value(predicted.get("start_line"))
    end = int_value(predicted.get("end_line"))
    exp_start = int_value(expected.get("start_line"))
    exp_end = int_value(expected.get("end_line"))
    return bool(start and end and exp_start and exp_end and start <= exp_end and exp_start <= end)


def infer_failure_type(top10: List[str]) -> str:
    return "no_file_candidates" if not top10 else "file_top10_miss"


def infer_method_failure_type(
    method_rank: int | None,
    method_groundtruth: List[Dict[str, Any]],
    method_items: List[Dict[str, Any]],
) -> str:
    if not method_groundtruth:
        return "missing_method_groundtruth"
    if method_rank is not None and method_rank <= 5:
        return "method_top5_hit"
    if method_rank is not None and method_rank <= 10:
        return "method_top10_hit"
    if not method_items:
        return "no_method_candidates"
    return "method_top5_miss"


def read_efficiency_parts(report: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
    efficiency = report.get("efficiency") if isinstance(report.get("efficiency"), dict) else {}
    usage = efficiency.get("llm_usage") if isinstance(efficiency.get("llm_usage"), dict) else {}
    file_efficiency = {
        "wall_time_sec": float_value(efficiency.get("total_wall_time_sec")),
        "stage_times_sec": read_stage_times(efficiency.get("stage_times_sec")),
        "model": str(usage.get("model") or ""),
        "llm_request_count": int_value(usage.get("request_count")),
        "input_tokens": int_value(usage.get("input_tokens")),
        "output_tokens": int_value(usage.get("output_tokens")),
        "total_tokens": int_value(usage.get("total_tokens")),
    }
    method_efficiency = {
        "wall_time_sec": 0.0,
        "stage_times_sec": {},
        "model": "",
        "llm_request_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    return file_efficiency, method_efficiency


def build_accuracy(results: List[BugResult], attr: str) -> AccuracyStat:
    total = len(results)
    hit_count = sum(int(getattr(item, attr)) for item in results)
    return AccuracyStat(hit=hit_count, total=total, percentage=percentage(hit_count, total))


def build_efficiency_summary(efficiencies: List[Dict[str, Any]]) -> EfficiencySummary:
    total = len(efficiencies)
    wall_time = sum(float_value(item.get("wall_time_sec")) for item in efficiencies)
    request_count = sum(int_value(item.get("llm_request_count")) for item in efficiencies)
    input_tokens = sum(int_value(item.get("input_tokens")) for item in efficiencies)
    output_tokens = sum(int_value(item.get("output_tokens")) for item in efficiencies)
    total_tokens = sum(int_value(item.get("total_tokens")) for item in efficiencies)

    stage_totals: Dict[str, float] = {}
    stage_counts: Dict[str, int] = {}
    model_counts: Dict[str, int] = {}
    for item in efficiencies:
        model = str(item.get("model") or "").strip()
        if model:
            model_counts[model] = model_counts.get(model, 0) + 1
        stage_times = item.get("stage_times_sec") if isinstance(item.get("stage_times_sec"), dict) else {}
        for key, value in stage_times.items():
            stage_totals[key] = stage_totals.get(key, 0.0) + float_value(value)
            stage_counts[key] = stage_counts.get(key, 0) + 1

    return EfficiencySummary(
        total_wall_time_sec=round(wall_time, 3),
        average_wall_time_sec=round(wall_time / total, 3) if total else 0.0,
        total_llm_requests=request_count,
        average_llm_requests=round(request_count / total, 3) if total else 0.0,
        total_input_tokens=input_tokens,
        average_input_tokens=round(input_tokens / total, 3) if total else 0.0,
        total_output_tokens=output_tokens,
        average_output_tokens=round(output_tokens / total, 3) if total else 0.0,
        total_tokens=total_tokens,
        average_total_tokens=round(total_tokens / total, 3) if total else 0.0,
        stage_time_totals_sec={key: round(value, 3) for key, value in sorted(stage_totals.items())},
        stage_time_averages_sec={
            key: round(stage_totals[key] / stage_counts[key], 3)
            for key in sorted(stage_totals)
            if stage_counts.get(key)
        },
        model_counts=dict(sorted(model_counts.items())),
    )


def build_failure_type_counts(results: List[BugResult]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in results:
        counts[item.failure_type] = counts.get(item.failure_type, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def build_method_failure_type_counts(results: List[BugResult]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in results:
        counts[item.method_failure_type] = counts.get(item.method_failure_type, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0])))


def read_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return unique_keep_order([str(item or "").strip() for item in value if str(item or "").strip()])


def read_stage_times(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): float_value(item) for key, item in value.items()}


def normalize_method_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def percentage(hit_count: int, total: int) -> str:
    return f"{(hit_count / total * 100.0 if total else 0.0):.2f}%"


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def float_value(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def main() -> None:
    args = parse_args()
    if args.limit < 0:
        raise RuntimeError("--limit must be >= 0")

    cfg = load_runtime_config()
    output_path = Path(args.output).resolve() if args.output else cfg.paths.root / "graphlocator_rustc" / "result.json"
    bug_ids = select_bug_ids(args, read_bug_ids(cfg.paths.csv_file))

    results: List[BugResult] = []
    efficiencies: List[Dict[str, Any]] = []
    method_efficiencies: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for bug_id in bug_ids:
        report_path = cfg.report_dir / f"{bug_id}.json"
        groundtruth_path = cfg.paths.groundtruth_dir / f"{bug_id}.json"
        method_groundtruth_path = cfg.paths.method_groundtruth_dir / f"{bug_id}.json"
        missing = [str(path) for path in (report_path, groundtruth_path) if not path.exists()]
        if missing:
            if args.strict:
                raise FileNotFoundError(f"missing inputs for bug_id={bug_id}: {', '.join(missing)}")
            skipped.append({"bug_id": bug_id, "missing": ", ".join(missing)})
            continue
        report = load_json(report_path)
        result = compute_bug_result(
            report=report,
            groundtruth_files=load_groundtruth_files(groundtruth_path),
            method_groundtruth=load_method_groundtruth(method_groundtruth_path),
            bug_id=bug_id,
        )
        results.append(result)
        file_efficiency, method_efficiency = read_efficiency_parts(report)
        efficiencies.append(file_efficiency)
        method_efficiencies.append(method_efficiency)

    result_obj: Dict[str, Any] = {
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
        "efficiency": asdict(build_efficiency_summary(efficiencies)),
        "method_efficiency": asdict(build_efficiency_summary(method_efficiencies)),
        "failure_type_counts": build_failure_type_counts(results),
        "method_failure_type_counts": build_method_failure_type_counts(results),
        "bugs": [asdict(item) for item in results],
    }
    if skipped:
        result_obj["skipped"] = skipped

    write_json(output_path, result_obj)
    status(f"result saved: {output_path}")
    status(f"done: rows={len(results)} skipped={len(skipped)}")


if __name__ == "__main__":
    main()
