from __future__ import annotations

import argparse
from datetime import datetime
from typing import Dict, List

from src.common.config import load_config
from src.common.csv_io import read_dict_csv
from src.common.json_io import unique_keep_order
from src.localize.pipeline import build_pipeline_from_config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GCC CBI method-level localization")
    parser.add_argument("--bug-id", default="", help="Run one GCC bug instance id")
    parser.add_argument("--bug-ids", default="", help="Run comma-separated GCC bug instance ids")
    parser.add_argument("--limit", type=int, default=0, help="Run the first N rows from gccbugs.csv")
    parser.add_argument("--skip-existing", action="store_true", help="Skip bugs whose report already exists")
    parser.add_argument("--skip-checkout", action="store_true", help="Do not checkout gcc/ before localization")
    parser.add_argument(
        "--clean-checkout",
        action="store_true",
        help="Before each checkout, run git reset --hard and git clean -ffdx inside gcc/.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    rows = read_dict_csv(cfg["paths"]["dataset_csv"])
    selected = select_rows(args, rows)
    if args.skip_existing:
        report_dir = cfg["paths"]["report_dir"]
        selected = [row for row in selected if not (report_dir / f"{row['instance_id']}.json").exists()]

    if not selected:
        log("selected bugs: 0")
        log(f"report dir: {cfg['paths']['report_dir']}")
        return

    pipeline = build_pipeline_from_config()

    log(f"selected bugs: {len(selected)}")
    log(f"model: {pipeline.client.model}")
    log(f"report dir: {pipeline.paths.report_dir}")
    total = len(selected)
    for index, row in enumerate(selected, start=1):
        bug_id = row["instance_id"]
        log(f"[{index}/{total}] start bug_id={bug_id}")
        report = pipeline.run_bug(
            bug_id=bug_id,
            checkout_ref_or_commit="" if args.skip_checkout else row.get("base_commit", ""),
            clean_checkout=bool(args.clean_checkout),
        )
        log(f"[{index}/{total}] done bug_id={report.get('bug_id', bug_id)}")


def select_rows(args: argparse.Namespace, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    by_id: Dict[str, Dict[str, str]] = {}
    for row in rows:
        bug_id = str(row.get("instance_id") or "").strip()
        if bug_id and bug_id not in by_id:
            by_id[bug_id] = row

    requested: List[str] = []
    if args.bug_id.strip():
        requested.append(args.bug_id.strip())
    if args.bug_ids.strip():
        requested.extend(item.strip() for item in args.bug_ids.split(",") if item.strip())
    requested = unique_keep_order(requested)
    if requested:
        missing = [bug_id for bug_id in requested if bug_id not in by_id]
        if missing:
            raise RuntimeError(f"bug_id not found in gccbugs.csv: {', '.join(missing)}")
        return [by_id[bug_id] for bug_id in requested]

    selected = rows[: args.limit] if args.limit else rows
    deduped: List[Dict[str, str]] = []
    seen: set[str] = set()
    for row in selected:
        bug_id = str(row.get("instance_id") or "").strip()
        if not bug_id or bug_id in seen:
            continue
        seen.add(bug_id)
        deduped.append(row)
    return deduped


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


if __name__ == "__main__":
    main()
