from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from .config import load_config
from .io_utils import load_json, write_json


REUSED_FIELDS = (
    "found_files",
    "node_info",
    "candidate_file_count",
    "document_count",
    "embedding_model",
    "similarity_top_k",
    "query_original_chars",
    "query_embedding_chars",
    "query_truncated",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract frozen Agentless embedding retrieval artifacts")
    parser.add_argument("--report-dir", default="", help="Run1 report directory; defaults to paths.report_dir")
    parser.add_argument("--embedding-dir", default="", help="Output directory; defaults to paths.embedding_dir")
    return parser.parse_args()


def extract_artifact(report: Dict[str, Any], *, report_path: Path) -> Dict[str, Any]:
    file_level = report.get("file_level")
    retrieval = file_level.get("retrieval") if isinstance(file_level, dict) else None
    if not isinstance(retrieval, dict):
        raise RuntimeError(f"Report has no file_level.retrieval object: {report_path}")
    if not isinstance(retrieval.get("found_files"), list) or not isinstance(retrieval.get("node_info"), list):
        raise RuntimeError(f"Report has invalid embedding retrieval data: {report_path}")

    artifact = {key: retrieval[key] for key in REUSED_FIELDS if key in retrieval}
    artifact["bug_id"] = str(report.get("bug_id") or report_path.stem)
    artifact["frozen_from_run1"] = True
    return artifact


def main() -> None:
    args = parse_args()
    cfg = load_config()
    report_dir = Path(args.report_dir).resolve() if args.report_dir else cfg.paths.report_dir
    embedding_dir = Path(args.embedding_dir).resolve() if args.embedding_dir else cfg.paths.embedding_dir

    report_paths = sorted(report_dir.glob("*.json"), key=lambda path: path.stem)
    if not report_paths:
        raise RuntimeError(f"No report JSON files found in {report_dir}")

    written = 0
    for report_path in report_paths:
        report = load_json(report_path)
        if not isinstance(report, dict):
            raise RuntimeError(f"Report root must be an object: {report_path}")
        artifact = extract_artifact(report, report_path=report_path)
        write_json(embedding_dir / f"{artifact['bug_id']}.json", artifact)
        written += 1

    print(f"extracted {written} embedding retrieval artifacts to {embedding_dir}")


if __name__ == "__main__":
    main()
