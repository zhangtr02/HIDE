from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from config import load_runtime_config
    from context import BugRecord, read_bug_records, unique_keep_order
else:
    from .config import load_runtime_config
    from .context import BugRecord, read_bug_records, unique_keep_order


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run GraphLocator-rustc bug isolation")
    parser.add_argument("--bug-id", default="", help="Run one bug id")
    parser.add_argument("--bug-ids", default="", help="Run comma-separated bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Run the first N rows from rustcbugs.csv")
    parser.add_argument("--skip-existing", action="store_true", help="Skip bugs whose report already exists")
    parser.add_argument("--no-checkout", action="store_true", help="Use current rust repo checkout")
    return parser.parse_args()


def status(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def select_bug_records(args: argparse.Namespace, rows: List[BugRecord]) -> List[BugRecord]:
    row_by_bug: Dict[str, BugRecord] = {}
    for row in rows:
        row_by_bug.setdefault(row.bug_id, row)

    requested: List[str] = []
    if args.bug_id.strip():
        requested.append(args.bug_id.strip())
    if args.bug_ids.strip():
        requested.extend(item.strip() for item in args.bug_ids.split(",") if item.strip())
    requested = unique_keep_order(requested)

    if requested:
        missing = [bug_id for bug_id in requested if bug_id not in row_by_bug]
        if missing:
            raise RuntimeError(f"bug_id not found in rustcbugs.csv: {', '.join(missing)}")
        return [row_by_bug[bug_id] for bug_id in requested]

    selected_rows = rows[: args.limit] if args.limit else rows
    selected: List[BugRecord] = []
    seen: set[str] = set()
    for row in selected_rows:
        if row.bug_id in seen:
            continue
        seen.add(row.bug_id)
        selected.append(row)
    return selected


def is_complete_report(path: Path, *, require_method_level: bool) -> bool:
    if not path.exists():
        return False
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return False
    if not isinstance(obj, dict) or not isinstance(obj.get("final_top10"), list):
        return False
    if not require_method_level:
        return True
    marker = obj.get("method_level_enabled")
    if marker is False:
        return False
    return bool(
        (
            isinstance(obj.get("final_method_top10"), list)
            or len(obj.get("final_method_items") if isinstance(obj.get("final_method_items"), list) else []) >= 10
            or len(
                obj.get("method_level", {}).get("found_methods", [])
                if isinstance(obj.get("method_level"), dict)
                and isinstance(obj.get("method_level", {}).get("found_methods"), list)
                else []
            )
            >= 10
        )
    )


def main() -> None:
    args = parse_args()
    if args.limit < 0:
        raise RuntimeError("--limit must be >= 0")

    if __package__ in (None, ""):
        from pipeline import GraphLocatorRustcPipeline, PipelineOptions
    else:
        from .pipeline import GraphLocatorRustcPipeline, PipelineOptions

    cfg = load_runtime_config()
    selected = select_bug_records(args, read_bug_records(cfg.paths.csv_file))
    if args.skip_existing:
        selected = [
            row for row in selected
            if not is_complete_report(
                cfg.report_dir / f"{row.bug_id}.json",
                require_method_level=cfg.graphlocator.run_method_level,
            )
        ]

    total = len(selected)
    status(f"selected bugs: {total}")
    status(f"model: {cfg.llm.model}")
    status(f"method level: {'enabled' if cfg.graphlocator.run_method_level else 'disabled'}")
    status(f"report dir: {cfg.report_dir}")
    status(f"graph cache dir: {cfg.graph_cache_dir}")

    pipeline = GraphLocatorRustcPipeline(cfg, PipelineOptions(checkout=not args.no_checkout))
    for index, row in enumerate(selected, start=1):
        start = time.time()
        status(f"[{index}/{total}] start bug_id={row.bug_id}")
        pipeline.run(row.bug_id)
        status(f"[{index}/{total}] done bug_id={row.bug_id} elapsed={round(time.time() - start, 2)}s")


if __name__ == "__main__":
    main()
