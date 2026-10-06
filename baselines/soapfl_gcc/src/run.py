from __future__ import annotations

import argparse
from datetime import datetime
from typing import Dict, List

from .config import load_config
from .io_utils import read_gccbugs_csv, unique_keep_order
from .llm import LLMClient
from .pipeline import PipelineOptions, SoapFLGccPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SoapFL-GCC method-level localization")
    parser.add_argument("--bug-id", default="", help="Run one GCC bug instance id")
    parser.add_argument("--bug-ids", default="", help="Run comma-separated GCC bug instance ids")
    parser.add_argument("--limit", type=int, default=0, help="Run the first N rows from gccbugs_200.csv")
    parser.add_argument("--skip-existing", action="store_true", help="Skip bugs whose report already exists")
    parser.add_argument("--skip-checkout", action="store_true", help="Do not checkout gcc/ before localization")
    return parser.parse_args()


def select_rows(args: argparse.Namespace, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    row_by_bug: Dict[str, Dict[str, str]] = {}
    for row in rows:
        bug_id = str(row.get("instance_id") or "").strip()
        if bug_id:
            row_by_bug.setdefault(bug_id, row)

    requested: List[str] = []
    if args.bug_id.strip():
        requested.append(args.bug_id.strip())
    if args.bug_ids.strip():
        requested.extend(item.strip() for item in args.bug_ids.split(",") if item.strip())
    requested = unique_keep_order(requested)
    if requested:
        missing = [bug_id for bug_id in requested if bug_id not in row_by_bug]
        if missing:
            raise RuntimeError(f"bug_id not found in dataset CSV: {', '.join(missing)}")
        return [row_by_bug[bug_id] for bug_id in requested]

    selected = rows[: args.limit] if args.limit else rows
    deduped: List[Dict[str, str]] = []
    seen: set[str] = set()
    for row in selected:
        bug_id = str(row.get("instance_id") or "").strip()
        if bug_id in seen:
            continue
        seen.add(bug_id)
        deduped.append(row)
    return deduped


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def main() -> None:
    args = parse_args()
    if args.limit < 0:
        raise RuntimeError("--limit must be >= 0")

    cfg = load_config()
    selected = select_rows(args, read_gccbugs_csv(cfg.paths.csv_file))
    if args.skip_existing:
        selected = [row for row in selected if not (cfg.paths.report_dir / f"{row['instance_id']}.json").exists()]

    client = LLMClient(cfg.llm)
    pipeline = SoapFLGccPipeline(cfg=cfg, client=client, options=PipelineOptions(checkout=not args.skip_checkout))

    total = len(selected)
    log(f"selected bugs: {total}")
    log(f"model: {cfg.llm.model}")
    log(f"report dir: {cfg.paths.report_dir}")
    for index, row in enumerate(selected, start=1):
        bug_id = row["instance_id"]
        log(f"[{index}/{total}] start bug_id={bug_id}")
        report = pipeline.run_bug(bug_id=bug_id, base_commit=row.get("base_commit", ""))
        log(f"[{index}/{total}] done bug_id={report.get('bug_id', bug_id)}")


if __name__ == "__main__":
    main()

