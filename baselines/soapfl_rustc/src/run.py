from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

from .config import load_config
from .io_utils import read_rustcbugs_csv, unique_keep_order
from .llm import LLMClient
from .pipeline import PipelineOptions, SoapFLRustcPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SoapFL-rustc bug isolation")
    parser.add_argument("--bug-id", default="", help="Run one bug id")
    parser.add_argument("--bug-ids", default="", help="Run comma-separated bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Run first N rows from rustcbugs.csv")
    parser.add_argument("--skip-existing", action="store_true", help="Skip bugs whose report already exists")
    parser.add_argument("--skip-checkout", action="store_true", help="Do not checkout rust/ before localization")
    parser.add_argument("--build-if-log-missing", action="store_true", help="Run cargo build if logs/<bug_id>.log is missing")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.limit < 0:
        raise RuntimeError("--limit must be >= 0")

    cfg = load_config()
    rows = select_rows(args, read_rustcbugs_csv(cfg.paths.csv_file))
    if args.skip_existing:
        rows = [
            row
            for row in rows
            if not is_complete_report(
                cfg.paths.report_dir / f"{row[0]}.json",
                require_method_level=cfg.soapfl.run_method_level,
            )
        ]

    client = LLMClient(cfg.llm)
    pipeline = SoapFLRustcPipeline(
        cfg=cfg,
        client=client,
        options=PipelineOptions(
            checkout=not bool(args.skip_checkout),
            build_if_log_missing=bool(args.build_if_log_missing),
        ),
    )

    total = len(rows)
    log(f"selected bugs: {total}")
    log(f"model: {cfg.llm.model}")
    log(f"method level: {'enabled' if cfg.soapfl.run_method_level else 'disabled'}")
    log(f"report dir: {cfg.paths.report_dir}")
    for index, (bug_id, toolchain, build_args) in enumerate(rows, start=1):
        log(f"[{index}/{total}] start bug_id={bug_id}")
        pipeline.run_bug(bug_id=bug_id, toolchain=toolchain, build_args=build_args)
        log(f"[{index}/{total}] done bug_id={bug_id}")


def select_rows(args: argparse.Namespace, rows: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    row_by_bug: Dict[str, Tuple[str, str, str]] = {}
    for bug_id, toolchain, build_args in rows:
        row_by_bug.setdefault(bug_id, (bug_id, toolchain, build_args))

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

    selected = rows[: args.limit] if args.limit else rows
    deduped: List[Tuple[str, str, str]] = []
    seen: set[str] = set()
    for bug_id, toolchain, build_args in selected:
        if bug_id in seen:
            continue
        seen.add(bug_id)
        deduped.append((bug_id, toolchain, build_args))
    return deduped


def log(message: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}", flush=True)


def is_complete_report(path: Path, *, require_method_level: bool) -> bool:
    if not path.exists():
        return False
    try:
        report = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return False
    if not isinstance(report, dict) or not isinstance(report.get("final_top10"), list):
        return False
    if not require_method_level:
        return True
    marker = report.get("method_level_enabled")
    if marker is not None:
        return marker is True
    return isinstance(report.get("final_method_top10"), list)


if __name__ == "__main__":
    main()
