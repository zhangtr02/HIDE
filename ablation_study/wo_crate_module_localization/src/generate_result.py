from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.config import load_config
from src.common.json_io import load_json, unique_keep_order, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate file-level ablation result.json")
    parser.add_argument("--config", default="", help="Config YAML path, default is config/config.yaml")
    parser.add_argument("--bug-id", default="", help="Evaluate one rustc bug id")
    parser.add_argument("--bug-ids", default="", help="Evaluate comma-separated rustc bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Evaluate first N rows from rustcbugs.csv")
    parser.add_argument("--strict", action="store_true", help="Fail if any report or groundtruth file is missing")
    parser.add_argument("--report-dir", default="", help="Override paths.report_dir from config")
    parser.add_argument("--output", default="", help="Output path, default is project root / result.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config or None)
    paths = cfg["paths"]
    if args.report_dir.strip():
        paths["report_dir"] = cfg["_project_root"] / args.report_dir.strip()
    output_path = Path(args.output).resolve() if args.output else paths["report_dir"].parent / "result.json"
    bug_ids = select_bug_ids(args, read_rustc_bug_ids(paths["csv_file"]))

    results: List[Dict[str, Any]] = []
    efficiencies: List[Dict[str, Any]] = []
    skipped: List[Dict[str, str]] = []
    for bug_id in bug_ids:
        report_path = paths["report_dir"] / f"{bug_id}.json"
        groundtruth_path = paths["groundtruth_dir"] / f"{bug_id}.json"
        missing = [str(path) for path in (report_path, groundtruth_path) if not path.exists()]
        if missing:
            if args.strict:
                raise FileNotFoundError(f"missing inputs for bug_id={bug_id}: {', '.join(missing)}")
            skipped.append({"bug_id": bug_id, "missing": ", ".join(missing)})
            continue
        report = load_json(report_path)
        groundtruth = load_groundtruth(groundtruth_path)
        result = build_bug_result(report=report, bug_id=bug_id, groundtruth=groundtruth)
        results.append(result)
        efficiencies.append(read_efficiency(report))

    result_obj: Dict[str, Any] = {
        "evaluated_bug_count": len(results),
        "skipped_bug_count": len(skipped),
        "top1_accuracy": accuracy(results, "top1_hit"),
        "top3_accuracy": accuracy(results, "top3_hit"),
        "top5_accuracy": accuracy(results, "top5_hit"),
        "top10_accuracy": accuracy(results, "top10_hit"),
        "efficiency": build_efficiency_summary(efficiencies),
        "failure_type_counts": count_values(results, "failure_type"),
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
) -> Dict[str, Any]:
    selected_files = selected_file_paths(report)[:10]
    position = first_position(selected_files, groundtruth["files"])
    return {
        "bug_id": bug_id,
        "top1_hit": int(position == 1),
        "top3_hit": int(position is not None and position <= 3),
        "top5_hit": int(position is not None and position <= 5),
        "top10_hit": int(position is not None and position <= 10),
        "groundtruth_position": position,
        "failure_type": "top10_hit" if position is not None else "global_file_localization_miss",
        "top10": selected_files,
        "groundtruth_files": groundtruth["files"],
        **read_efficiency(report),
    }


def load_groundtruth(path: Path) -> Dict[str, List[str]]:
    obj = load_json(path)
    files = read_string_list(
        obj.get("compiler_rs_files")
        or obj.get("buggy_files")
        or obj.get("all_buggy_files")
    )
    if not files:
        raise RuntimeError(f"missing compiler_rs_files in {path}")
    return {"files": files}


def selected_file_paths(report: Dict[str, Any]) -> List[str]:
    top10 = read_string_list(report.get("final_top10"))
    if top10:
        return top10
    stage3 = report.get("stage3") if isinstance(report.get("stage3"), dict) else {}
    items = stage3.get("doc_screened_files") if isinstance(stage3.get("doc_screened_files"), list) else []
    out: List[str] = []
    for item in items:
        text = str(item.get("id") if isinstance(item, dict) else item or "").strip()
        if text:
            out.append(text)
    return unique_keep_order(out)


def first_position(predicted: List[str], expected: Iterable[str]) -> int | None:
    expected_set = {item for item in expected if item}
    for index, item in enumerate(predicted, start=1):
        if item in expected_set:
            return index
    return None


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
