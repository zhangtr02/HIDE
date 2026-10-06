from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.json_io import load_json_if_exists, write_json


def write_report(report_dir: Path, report: Dict[str, Any]) -> Path:
    bug_id = str(report.get("bug_id") or "").strip()
    if not bug_id:
        raise RuntimeError("report missing bug_id")
    path = report_dir / f"{bug_id}.json"
    write_json(path, report)
    return path


def attach_diagnosis(
    report: Dict[str, Any],
    *,
    groundtruth_dir: Path,
    method_groundtruth_dir: Path,
) -> None:
    bug_id = str(report.get("bug_id") or "").strip()
    if not bug_id:
        return
    groundtruth = load_json_if_exists(groundtruth_dir / f"{bug_id}.json") or {}
    method_groundtruth = load_json_if_exists(method_groundtruth_dir / f"{bug_id}.json") or {}
    gt_files = read_string_list(
        groundtruth.get("compiler_rs_files")
        or groundtruth.get("buggy_files")
        or groundtruth.get("all_buggy_files")
    )
    gt_methods = method_groundtruth.get("methods") if isinstance(method_groundtruth.get("methods"), list) else []
    top10 = read_string_list(report.get("final_top10"))
    ranked_methods = report.get("stage4", {}).get("ranked_methods") if isinstance(report.get("stage4"), dict) else []
    ranked_method_items = [item for item in ranked_methods if isinstance(item, dict)]
    file_rank = first_position(top10, gt_files)
    method_rank = first_method_rank(ranked_method_items, [item for item in gt_methods if isinstance(item, dict)])
    report["diagnosis"] = {
        "groundtruth_files": gt_files,
        "groundtruth_file_rank": file_rank,
        "file_top1_hit": file_rank == 1,
        "file_top3_hit": file_rank is not None and file_rank <= 3,
        "file_top5_hit": file_rank is not None and file_rank <= 5,
        "file_top10_hit": file_rank is not None and file_rank <= 10,
        "method_groundtruth_count": len(gt_methods),
        "method_groundtruth_rank": method_rank,
        "method_top1_hit": method_rank == 1,
        "method_top3_hit": method_rank is not None and method_rank <= 3,
        "method_top5_hit": method_rank is not None and method_rank <= 5,
        "method_top10_hit": method_rank is not None and method_rank <= 10,
    }


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


def read_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
