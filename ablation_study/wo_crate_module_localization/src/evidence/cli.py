from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List

from src.common.config import load_config
from src.common.json_io import unique_keep_order, write_json
from src.evidence.extractor import EvidenceExtractor


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract structured evidence from rustc bug artifacts")
    parser.add_argument("--bug-id", default="", help="Extract one rustc bug id")
    parser.add_argument("--bug-ids", default="", help="Extract comma-separated rustc bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Extract first N rows from rustcbugs.csv")
    parser.add_argument("--skip-existing", action="store_true", help="Skip bugs whose evidence JSON already exists")
    parser.add_argument("--force", action="store_true", help="Overwrite existing evidence JSON")
    parser.add_argument("--output-dir", default="", help="Override config paths.evidence_dir")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    paths = cfg["paths"]
    root: Path = cfg["_project_root"]
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else paths.get("evidence_dir", root / "evidence")
    if not output_dir.is_absolute():
        output_dir = root / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = select_rows(args, read_rustc_rows(paths["csv_file"]))
    if args.skip_existing and not args.force:
        rows = [row for row in rows if not (output_dir / f"{row['bug_id']}.json").exists()]

    extractor = EvidenceExtractor(
        log_dir=paths["log_dir"],
        query_trace_dir=paths["query_trace_dir"],
    )
    print(f"selected bugs: {len(rows)}")
    print(f"evidence dir: {output_dir}")

    for index, row in enumerate(rows, start=1):
        bug_id = row["bug_id"]
        evidence = extractor.extract(
            bug_id=bug_id,
            toolchain=row.get("toolchain", ""),
            build_args=row.get("build_args", ""),
        )
        write_json(output_dir / f"{bug_id}.json", evidence)
        query_available = bool(evidence.get("query_trace", {}).get("available"))
        log_kind = str(evidence.get("execution", {}).get("kind") or "unknown")
        print(f"[{index}/{len(rows)}] {bug_id}: log={log_kind} query_trace={query_available}", flush=True)


def select_rows(args: argparse.Namespace, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    by_id: Dict[str, Dict[str, str]] = {}
    for row in rows:
        bug_id = str(row.get("bug_id") or "").strip()
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
            raise RuntimeError(f"bug_id not found in rustcbugs.csv: {', '.join(missing)}")
        return [by_id[bug_id] for bug_id in requested]

    selected = rows[: args.limit] if args.limit else rows
    deduped: List[Dict[str, str]] = []
    seen: set[str] = set()
    for row in selected:
        bug_id = str(row.get("bug_id") or "").strip()
        if not bug_id or bug_id in seen:
            continue
        seen.add(bug_id)
        deduped.append(row)
    return deduped


def read_rustc_rows(path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [item.strip() for item in line.split(",")]
        if len(parts) < 2:
            continue
        rows.append({"bug_id": parts[0], "toolchain": parts[1], "build_args": ",".join(parts[2:])})
    return rows


if __name__ == "__main__":
    main()
