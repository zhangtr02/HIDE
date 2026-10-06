from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import load_config
from src.common.csv_io import read_dict_csv
from src.common.gcc_modules import SPLIT_TOP_LEVEL_MODULES, root_module_id
from src.common.json_io import load_json, load_json_if_exists, unique_keep_order, write_json


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
    stage1_hit: int
    stage2_hit: int
    stage3_hit: int
    stage4_hit: int
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
    parser = argparse.ArgumentParser(description="Generate result.json from GCC CBI localization reports")
    parser.add_argument("--bug-id", default="", help="Evaluate one GCC bug instance id")
    parser.add_argument("--bug-ids", default="", help="Evaluate comma-separated GCC bug instance ids")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate the first N rows from gccbugs.csv")
    parser.add_argument("--strict", action="store_true", help="Fail if any report or groundtruth file is missing")
    parser.add_argument("--output", default="", help="Output path, default is result.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 0:
        raise RuntimeError("--limit must be >= 0")

    cfg = load_config()
    paths = cfg["paths"]
    root: Path = cfg["_project_root"]
    output_path = Path(args.output).resolve() if args.output else root / "result.json"

    bug_ids = select_bug_ids(args, read_dict_csv(paths["dataset_csv"]))
    results: List[BugResult] = []
    report_efficiencies: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []

    for bug_id in bug_ids:
        report_path = paths["report_dir"] / f"{bug_id}.json"
        groundtruth_path = paths["groundtruth_dir"] / f"{bug_id}.json"
        method_groundtruth_path = paths["method_groundtruth_dir"] / f"{bug_id}.json"
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
        report_efficiencies.append(read_efficiency(report))

    result_obj: Dict[str, Any] = {
        "evaluated_bug_count": len(results),
        "skipped_bug_count": len(skipped),
        "stage1_accuracy": asdict(build_accuracy(results, "stage1_hit")),
        "stage2_accuracy": asdict(build_accuracy(results, "stage2_hit")),
        "stage3_accuracy": asdict(build_accuracy(results, "stage3_hit")),
        "stage4_accuracy": asdict(build_accuracy(results, "stage4_hit")),
        "top1_accuracy": asdict(build_accuracy(results, "top1_hit")),
        "top3_accuracy": asdict(build_accuracy(results, "top3_hit")),
        "top5_accuracy": asdict(build_accuracy(results, "top5_hit")),
        "top10_accuracy": asdict(build_accuracy(results, "top10_hit")),
        "method_top1_accuracy": asdict(build_accuracy(results, "method_top1_hit")),
        "method_top3_accuracy": asdict(build_accuracy(results, "method_top3_hit")),
        "method_top5_accuracy": asdict(build_accuracy(results, "method_top5_hit")),
        "method_top10_accuracy": asdict(build_accuracy(results, "method_top10_hit")),
        "efficiency": asdict(build_efficiency_summary(report_efficiencies)),
        "failure_type_counts": build_failure_type_counts(results),
        "method_failure_type_counts": build_method_failure_type_counts(results),
        "bugs": [asdict(item) for item in results],
    }
    if skipped:
        result_obj["skipped"] = skipped

    write_json(output_path, result_obj)
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
            raise RuntimeError(f"bug_id not found in gccbugs.csv: {', '.join(missing)}")
        return requested
    return csv_bug_ids[: args.limit] if args.limit else csv_bug_ids


def build_bug_result(
    *,
    report: Dict[str, Any],
    bug_id: str,
    groundtruth: Dict[str, List[str]],
    method_groundtruth: List[Dict[str, Any]],
) -> BugResult:
    selected_files = selected_file_paths(report)
    top10 = selected_files[:10]
    ranked_methods = ranked_method_items(report)
    method_top10 = [method_identifier(item) for item in ranked_methods[:10]]
    selected_methods = selected_method_items(report)
    files = groundtruth["files"]
    modules = groundtruth["modules"]
    position = first_position(selected_files, files)
    method_rank = first_method_rank(ranked_methods[:10], method_groundtruth)
    efficiency = read_efficiency(report)

    stage1_hit = module_hit(stage_candidate_ids(report, "stage1", "selected_modules"), modules)
    stage2_hit = hit(stage_candidate_ids(report, "stage2", "selected_files"), files)
    stage3_hit = hit(stage_candidate_ids(report, "stage3", "doc_screened_files"), files)
    stage4_hit = first_method_rank(selected_methods, method_groundtruth) is not None

    return BugResult(
        bug_id=bug_id,
        stage1_hit=int(stage1_hit),
        stage2_hit=int(stage2_hit),
        stage3_hit=int(stage3_hit),
        stage4_hit=int(stage4_hit),
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
        failure_type=infer_failure_type(report, groundtruth, position),
        method_failure_type=infer_method_failure_type(report, method_groundtruth, selected_methods, ranked_methods),
        top10=top10,
        method_top10=method_top10,
        groundtruth_files=files,
        groundtruth_modules=modules,
        groundtruth_methods=method_groundtruth,
        wall_time_sec=efficiency["wall_time_sec"],
        llm_request_count=efficiency["llm_request_count"],
        input_tokens=efficiency["input_tokens"],
        output_tokens=efficiency["output_tokens"],
        total_tokens=efficiency["total_tokens"],
    )


def load_groundtruth(path: Path) -> Dict[str, List[str]]:
    obj = load_json(path)
    files = read_string_list(obj.get("buggy_files") or obj.get("all_buggy_files"))
    if not files:
        raise RuntimeError(f"missing buggy_files in {path}")
    modules = unique_keep_order(infer_module(file_id) for file_id in files) or read_string_list(obj.get("buggy_modules"))
    return {"files": files, "modules": modules}


def load_method_groundtruth(path: Path) -> List[Dict[str, Any]]:
    obj = load_json_if_exists(path)
    if not obj:
        return []
    methods = obj.get("methods") if isinstance(obj.get("methods"), list) else []
    return [item for item in methods if isinstance(item, dict)]


def selected_file_paths(report: Dict[str, Any]) -> List[str]:
    top10 = read_string_list(report.get("final_top10"))
    if top10:
        return top10
    stage3 = stage_candidate_ids(report, "stage3", "doc_screened_files")
    if stage3:
        return stage3
    stage2 = stage_candidate_ids(report, "stage2", "selected_files")
    if stage2:
        return stage2
    return read_string_list(report.get("final_top5"))


def ranked_method_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    stage4 = report.get("stage4") if isinstance(report.get("stage4"), dict) else {}
    ranked = stage4.get("ranked_methods") if isinstance(stage4.get("ranked_methods"), list) else []
    items = [item for item in ranked if isinstance(item, dict)]
    return sorted(items, key=lambda item: int_value(item.get("rank")) or 10**9)


def selected_method_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    stage4 = report.get("stage4") if isinstance(report.get("stage4"), dict) else {}
    selected = stage4.get("selected_methods") if isinstance(stage4.get("selected_methods"), list) else []
    return [item for item in selected if isinstance(item, dict)]


def method_identifier(item: Dict[str, Any]) -> str:
    method_id = str(item.get("id") or "").strip()
    if method_id:
        return method_id
    file_id = str(item.get("file") or "").strip()
    qualified_name = str(item.get("qualified_name") or item.get("item_name") or "").strip()
    start_line = item.get("start_line")
    end_line = item.get("end_line")
    return f"{file_id}::{qualified_name}@{start_line}-{end_line}"


def read_efficiency(report: Dict[str, Any]) -> Dict[str, Any]:
    efficiency = report.get("efficiency") if isinstance(report.get("efficiency"), dict) else {}
    usage = efficiency.get("llm_usage") if isinstance(efficiency.get("llm_usage"), dict) else {}
    return {
        "wall_time_sec": round(float_value(efficiency.get("total_wall_time_sec")), 3),
        "stage_times_sec": read_stage_times(efficiency.get("stage_times_sec")),
        "model": str(usage.get("model") or ""),
        "llm_request_count": int_value(usage.get("request_count")),
        "input_tokens": int_value(usage.get("input_tokens")),
        "output_tokens": int_value(usage.get("output_tokens")),
        "total_tokens": int_value(usage.get("total_tokens")),
    }


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


def build_accuracy(results: List[BugResult], attr: str) -> AccuracyStat:
    total = len(results)
    hit_count = sum(int(getattr(item, attr)) for item in results)
    return AccuracyStat(hit=hit_count, total=total, percentage=percentage(hit_count, total))


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


def infer_failure_type(report: Dict[str, Any], groundtruth: Dict[str, List[str]], position: int | None) -> str:
    if position is not None and position <= 10:
        return "top10_hit"
    stage1_ids = stage_candidate_ids(report, "stage1", "selected_modules")
    stage2_ids = stage_candidate_ids(report, "stage2", "selected_files")
    if not stage1_ids:
        return "no_stage1_candidates"
    if not module_hit(stage1_ids, groundtruth["modules"]):
        return "stage1_module_miss"
    if not stage2_ids:
        return "no_stage2_file_candidates"
    if not hit(stage2_ids, groundtruth["files"]):
        return "stage2_file_doc_miss"
    return "stage2_selection_order_miss"


def infer_method_failure_type(
    report: Dict[str, Any],
    method_groundtruth: List[Dict[str, Any]],
    selected_methods: List[Dict[str, Any]],
    ranked_methods: List[Dict[str, Any]],
) -> str:
    if not method_groundtruth:
        return "missing_method_groundtruth"
    method_rank = first_method_rank(ranked_methods, method_groundtruth)
    if method_rank is not None and method_rank <= 5:
        return "method_top5_hit"
    if method_rank is not None and method_rank <= 10:
        return "method_top10_hit"
    if not report.get("stage4", {}).get("method_input_files"):
        return "no_stage4_input_files"
    if not selected_methods:
        return "no_stage4_method_candidates"
    if first_method_rank(selected_methods, method_groundtruth) is None:
        return "stage4_method_selection_miss"
    return "stage4_method_rerank_miss"


def infer_module(file_id: str) -> str:
    parts = file_id.split("/")
    if len(parts) >= 2 and parts[0] == "gcc" and parts[1] == "testsuite":
        return ""
    if len(parts) == 2 and parts[0] == "gcc":
        return root_module_id(parts[1])
    if len(parts) >= 3 and parts[0] == "gcc":
        if parts[1] in SPLIT_TOP_LEVEL_MODULES:
            return "/".join(parts[:3]) if len(parts) >= 4 else f"gcc/{parts[1]}/root"
        return "/".join(parts[:2])
    return ""


def candidate_ids(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for item in value:
        if isinstance(item, dict):
            out.append(str(item.get("id") or item.get("path") or "").strip())
        else:
            out.append(str(item or "").strip())
    return unique_keep_order(out)


def stage_candidate_ids(report: Dict[str, Any], stage_name: str, field_name: str) -> List[str]:
    stage = report.get(stage_name)
    if not isinstance(stage, dict):
        return []
    return candidate_ids(stage.get(field_name))


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
        if any(method_matches(item, expected_item) for expected_item in expected):
            return index
    return None


def method_matches(predicted: Dict[str, Any], expected: Dict[str, Any]) -> bool:
    predicted_file = str(predicted.get("file") or "").strip()
    expected_file = str(expected.get("file") or "").strip()
    if not predicted_file or predicted_file != expected_file:
        return False
    predicted_qualified = normalize_method_name(predicted.get("qualified_name"))
    expected_qualified = normalize_method_name(expected.get("qualified_name"))
    if predicted_qualified and predicted_qualified == expected_qualified:
        return True
    predicted_name = normalize_method_name(predicted.get("item_name"))
    expected_name = normalize_method_name(expected.get("item_name"))
    return bool(predicted_name and predicted_name == expected_name)


def normalize_method_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def hit(predicted: Iterable[str], expected: Iterable[str]) -> bool:
    expected_set = {item for item in expected if item}
    return any(item in expected_set for item in predicted if item)


def module_hit(predicted: Iterable[str], expected: Iterable[str]) -> bool:
    expected_items = [item for item in expected if item]
    return any(module_matches(pred_item, exp_item) for pred_item in predicted if pred_item for exp_item in expected_items)


def module_matches(predicted: str, expected: str) -> bool:
    predicted = str(predicted or "").strip()
    expected = str(expected or "").strip()
    if not predicted or not expected:
        return False
    return predicted == expected or predicted.startswith(f"{expected}/") or expected.startswith(f"{predicted}/")


def read_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return unique_keep_order([str(item or "").strip() for item in value if str(item or "").strip()])


def read_stage_times(value: Any) -> Dict[str, float]:
    if not isinstance(value, dict):
        return {}
    return {str(key): round(float_value(item), 3) for key, item in value.items()}


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


def percentage(hit_count: int, total: int) -> str:
    return f"{(hit_count / total * 100.0 if total else 0.0):.2f}%"


if __name__ == "__main__":
    main()
