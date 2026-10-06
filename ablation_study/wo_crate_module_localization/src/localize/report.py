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


def attach_diagnosis(report: Dict[str, Any], *, groundtruth_dir: Path) -> None:
    bug_id = str(report.get("bug_id") or "").strip()
    if not bug_id:
        return
    groundtruth = load_json_if_exists(groundtruth_dir / f"{bug_id}.json") or {}
    gt_files = read_string_list(
        groundtruth.get("compiler_rs_files")
        or groundtruth.get("buggy_files")
        or groundtruth.get("all_buggy_files")
    )
    top10 = read_string_list(report.get("final_top10"))
    file_rank = first_position(top10, gt_files)
    report["diagnosis"] = {
        "groundtruth_files": gt_files,
        "groundtruth_file_rank": file_rank,
        "file_top1_hit": file_rank == 1,
        "file_top3_hit": file_rank is not None and file_rank <= 3,
        "file_top5_hit": file_rank is not None and file_rank <= 5,
        "file_top10_hit": file_rank is not None and file_rank <= 10,
    }


def first_position(predicted: List[str], expected: Iterable[str]) -> int | None:
    expected_set = {item for item in expected if item}
    for index, item in enumerate(predicted, start=1):
        if item in expected_set:
            return index
    return None


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
