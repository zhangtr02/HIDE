from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.config import load_config
from src.common.json_io import load_json, load_json_if_exists, unique_keep_order, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate rustc-cbi result.json from localization reports")
    parser.add_argument("--config", default="", help="Config YAML path, default is config/config.yaml")
    parser.add_argument("--bug-id", default="", help="Evaluate one rustc bug id")
    parser.add_argument("--bug-ids", default="", help="Evaluate comma-separated rustc bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate first N rows from rustcbugs.csv")
    parser.add_argument("--strict", action="store_true", help="Fail if any report or groundtruth file is missing")
    parser.add_argument("--report-dir", default="", help="Override paths.report_dir from config")
    parser.add_argument("--output", default="", help="Output path, default is paths.report_dir parent / result.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config or None)
    paths = cfg["paths"]
    if args.report_dir.strip():
        paths["report_dir"] = cfg["_project_root"] / args.report_dir.strip()
    root: Path = cfg["_project_root"]
    output_path = Path(args.output).resolve() if args.output else paths["report_dir"].parent / "result.json"
    bug_ids = select_bug_ids(args, read_rustc_bug_ids(paths["csv_file"]))

    results: List[Dict[str, Any]] = []
    efficiencies: List[Dict[str, Any]] = []
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
        result = build_bug_result(report=report, bug_id=bug_id, groundtruth=groundtruth, method_groundtruth=method_groundtruth)
        results.append(result)
        efficiencies.append(read_efficiency(report))

    result_obj: Dict[str, Any] = {
        "evaluated_bug_count": len(results),
        "skipped_bug_count": len(skipped),
        "stage1_accuracy": accuracy(results, "stage1_hit"),
        "stage2_accuracy": accuracy(results, "stage2_hit"),
        "stage3_accuracy": accuracy(results, "stage3_hit"),
        "stage4_accuracy": accuracy(results, "stage4_hit"),
        "top1_accuracy": accuracy(results, "top1_hit"),
        "top3_accuracy": accuracy(results, "top3_hit"),
        "top5_accuracy": accuracy(results, "top5_hit"),
        "top10_accuracy": accuracy(results, "top10_hit"),
        "method_top1_accuracy": accuracy(results, "method_top1_hit"),
        "method_top3_accuracy": accuracy(results, "method_top3_hit"),
        "method_top5_accuracy": accuracy(results, "method_top5_hit"),
        "method_top10_accuracy": accuracy(results, "method_top10_hit"),
        "efficiency": build_efficiency_summary(efficiencies),
        "failure_type_counts": count_values(results, "failure_type"),
        "method_failure_type_counts": count_values(results, "method_failure_type"),
        "bugs": results,
    }
    if skipped:
        result_obj["skipped"] = skipped
    write_json(output_path, result_obj)
    print(f"[result] saved {output_path}", flush=True)


def read_rustc_bug_ids(path: Path) -> List[str]:
    bug_ids: List[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = [part.strip() for part in line.split(",", 2)]
        if parts and parts[0]:
            bug_ids.append(parts[0])
    return unique_keep_order(bug_ids)


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


def build_bug_result(
    *,
    report: Dict[str, Any],
    bug_id: str,
    groundtruth: Dict[str, List[str]],
    method_groundtruth: List[Dict[str, Any]],
) -> Dict[str, Any]:
    selected_files = selected_file_paths(report)
    top10 = selected_files[:10]
    ranked_methods = ranked_method_items(report)
    method_top10 = [method_identifier(item) for item in ranked_methods[:10]]
    selected_methods = selected_method_items(report)
    files = groundtruth["files"]
    crates = groundtruth["crates"]
    modules = groundtruth["modules"]
    position = first_position(selected_files, files)
    method_rank = first_method_rank(ranked_methods[:10], method_groundtruth)
    eff = read_efficiency(report)
    stage1_skipped = is_stage_skipped(report, "stage1")
    stage1_hit = None if stage1_skipped else hit(stage_candidate_ids(report, "stage1", "selected_crates"), crates)
    stage2_hit = hit(stage_candidate_ids(report, "stage2", "selected_modules"), modules)
    stage3_hit = hit(stage_candidate_ids(report, "stage3", "doc_screened_files"), files)
    stage4_hit = first_method_rank(selected_methods, method_groundtruth) is not None
    return {
        "bug_id": bug_id,
        "stage1_skipped": int(stage1_skipped),
        "stage1_hit": None if stage1_skipped else int(bool(stage1_hit)),
        "stage2_hit": int(stage2_hit),
        "stage3_hit": int(stage3_hit),
        "stage4_hit": int(stage4_hit),
        "top1_hit": int(position == 1),
        "top3_hit": int(position is not None and position <= 3),
        "top5_hit": int(position is not None and position <= 5),
        "top10_hit": int(position is not None and position <= 10),
        "method_top1_hit": int(method_rank == 1),
        "method_top3_hit": int(method_rank is not None and method_rank <= 3),
        "method_top5_hit": int(method_rank is not None and method_rank <= 5),
        "method_top10_hit": int(method_rank is not None and method_rank <= 10),
        "groundtruth_position": position,
        "method_groundtruth_rank": method_rank,
        "failure_type": infer_failure_type(report, groundtruth, position),
        "method_failure_type": infer_method_failure_type(method_groundtruth, selected_methods, ranked_methods),
        "top10": top10,
        "method_top10": method_top10,
        "groundtruth_files": files,
        "groundtruth_crates": crates,
        "groundtruth_modules": modules,
        "groundtruth_methods": method_groundtruth,
        **eff,
    }


def load_groundtruth(path: Path) -> Dict[str, List[str]]:
    obj = load_json(path)
    files = read_string_list(obj.get("compiler_rs_files") or obj.get("buggy_files") or obj.get("all_buggy_files"))
    if not files:
        raise RuntimeError(f"missing compiler_rs_files in {path}")
    crates = unique_keep_order(infer_crate(file_id) for file_id in files)
    modules = read_string_list(obj.get("buggy_modules")) or unique_keep_order(infer_module(file_id) for file_id in files)
    return {"files": files, "crates": crates, "modules": modules}


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
    return []


def ranked_method_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    stage4 = report.get("stage4") if isinstance(report.get("stage4"), dict) else {}
    ranked = stage4.get("ranked_methods") if isinstance(stage4.get("ranked_methods"), list) else []
    return sorted([item for item in ranked if isinstance(item, dict)], key=lambda item: int_value(item.get("rank")) or 10**9)


def selected_method_items(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    stage4 = report.get("stage4") if isinstance(report.get("stage4"), dict) else {}
    selected = stage4.get("selected_methods") if isinstance(stage4.get("selected_methods"), list) else []
    return [item for item in selected if isinstance(item, dict)]


def stage_candidate_ids(report: Dict[str, Any], stage_name: str, key: str) -> List[str]:
    stage = report.get(stage_name) if isinstance(report.get(stage_name), dict) else {}
    items = stage.get(key) if isinstance(stage.get(key), list) else []
    out: List[str] = []
    for item in items:
        if isinstance(item, dict):
            text = str(item.get("id") or "").strip()
        else:
            text = str(item or "").strip()
        if text:
            out.append(text)
    return unique_keep_order(out)


def method_identifier(item: Dict[str, Any]) -> str:
    item_id = str(item.get("id") or "").strip()
    if item_id:
        return item_id
    file_id = str(item.get("file") or "").strip()
    qualified = str(item.get("qualified_name") or item.get("item_name") or "").strip()
    start = item.get("start_line")
    end = item.get("end_line")
    suffix = f"@{start}-{end}" if start and end else ""
    return f"{file_id}::{qualified}{suffix}" if file_id and qualified else item_id


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
    if str(predicted.get("file") or "").strip() != str(expected.get("file") or "").strip():
        return False
    predicted_qualified = normalize_name(predicted.get("qualified_name"))
    expected_qualified = normalize_name(expected.get("qualified_name"))
    if predicted_qualified and predicted_qualified == expected_qualified:
        return True
    return normalize_name(predicted.get("item_name")) == normalize_name(expected.get("item_name"))


def infer_failure_type(report: Dict[str, Any], groundtruth: Dict[str, List[str]], position: int | None) -> str:
    if position is not None:
        return "top10_hit" if position <= 10 else "ranked_below_top10"
    if not is_stage_skipped(report, "stage1") and not hit(stage_candidate_ids(report, "stage1", "selected_crates"), groundtruth["crates"]):
        return "stage1_crate_miss"
    if not hit(stage_candidate_ids(report, "stage2", "selected_modules"), groundtruth["modules"]):
        return "stage2_module_miss"
    if not hit(stage_candidate_ids(report, "stage3", "doc_screened_files"), groundtruth["files"]):
        return "stage3_file_screening_miss"
    return "top10_miss"


def infer_method_failure_type(
    method_groundtruth: List[Dict[str, Any]],
    selected_methods: List[Dict[str, Any]],
    ranked_methods: List[Dict[str, Any]],
) -> str:
    if not method_groundtruth:
        return "no_method_groundtruth"
    rank = first_method_rank(ranked_methods[:10], method_groundtruth)
    if rank is not None:
        return "method_top10_hit"
    if first_method_rank(selected_methods, method_groundtruth) is None:
        return "stage4_method_selection_miss"
    return "stage4_method_rerank_miss"


def hit(predicted: Iterable[str], expected: Iterable[str]) -> bool:
    expected_set = {item for item in expected if item}
    return any(item in expected_set for item in predicted)


def is_stage_skipped(report: Dict[str, Any], stage_name: str) -> bool:
    stage = report.get(stage_name) if isinstance(report.get(stage_name), dict) else {}
    return bool(stage.get("skipped"))


def infer_crate(file_id: str) -> str:
    parts = Path(file_id).parts
    return parts[1] if len(parts) >= 2 and parts[0] == "compiler" else ""


def infer_module(file_id: str) -> str:
    parts = Path(file_id).parts
    if len(parts) < 4 or parts[0] != "compiler":
        return ""
    crate = parts[1]
    rel = list(parts[3:])
    if not rel:
        return ""
    first = Path(rel[0]).stem
    if first in {"lib", "main", "mod"} and len(rel) > 1:
        first = Path(rel[1]).stem
    return f"{crate}::{first}" if first else crate


def read_efficiency(report: Dict[str, Any]) -> Dict[str, Any]:
    efficiency = report.get("efficiency") if isinstance(report.get("efficiency"), dict) else {}
    usage = efficiency.get("llm_usage") if isinstance(efficiency.get("llm_usage"), dict) else {}
    return {
        "wall_time_sec": float(efficiency.get("total_wall_time_sec") or 0.0),
        "llm_request_count": int_value(usage.get("request_count")),
        "input_tokens": int_value(usage.get("input_tokens")),
        "output_tokens": int_value(usage.get("output_tokens")),
        "total_tokens": int_value(usage.get("total_tokens")),
    }


def build_efficiency_summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(items) or 1
    totals = {
        "total_wall_time_sec": sum(float(item.get("wall_time_sec") or 0.0) for item in items),
        "total_llm_requests": sum(int(item.get("llm_request_count") or 0) for item in items),
        "total_input_tokens": sum(int(item.get("input_tokens") or 0) for item in items),
        "total_output_tokens": sum(int(item.get("output_tokens") or 0) for item in items),
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in items),
    }
    return {
        **totals,
        "average_wall_time_sec": totals["total_wall_time_sec"] / n,
        "average_llm_requests": totals["total_llm_requests"] / n,
        "average_input_tokens": totals["total_input_tokens"] / n,
        "average_output_tokens": totals["total_output_tokens"] / n,
        "average_total_tokens": totals["total_tokens"] / n,
    }


def accuracy(results: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    total = len(results)
    value = sum(int(item.get(key) or 0) for item in results)
    return {"hit": value, "total": total, "percentage": f"{(value / total * 100) if total else 0:.2f}%"}


def count_values(results: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in results:
        value = str(item.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return counts


def normalize_name(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def read_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return unique_keep_order([str(item or "").strip() for item in value if str(item or "").strip()])


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


if __name__ == "__main__":
    main()
