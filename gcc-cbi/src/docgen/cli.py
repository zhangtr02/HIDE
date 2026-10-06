from __future__ import annotations

import argparse
from typing import Dict, List

from src.common.config import load_config
from src.common.csv_io import read_dict_csv
from src.common.json_io import unique_keep_order
from src.common.llm import LLMClient
from src.docgen.generator import DocGenerator
from src.docgen.pipeline import DocgenOptions, DocgenPaths, DocgenPipeline


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate reusable GCC responsibility docs.")
    parser.add_argument("--bug-id", default="", help="Generate docs for the commit used by one GCC bug id.")
    parser.add_argument("--bug-ids", default="", help="Generate docs for comma-separated GCC bug ids.")
    parser.add_argument("--commit", default="", help="Generate docs for one explicit git ref.")
    parser.add_argument("--limit", type=int, default=0, help="Use the first N rows from gccbugs.csv.")
    parser.add_argument(
        "--level",
        choices=["all", "file", "same-name", "module"],
        default="all",
        help="Doc generation level.",
    )
    parser.add_argument("--no-checkout", action="store_true", help="Scan current GCC checkout without git checkout.")
    parser.add_argument(
        "--clean-checkout",
        action="store_true",
        help="Before each checkout, run git reset --hard and git clean -ffdx inside the GCC repo, then force checkout.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    paths_cfg = cfg["paths"]
    docgen_cfg = cfg.get("docgen") or {}
    rows = read_dict_csv(paths_cfg["dataset_csv"])

    client = LLMClient.from_config(cfg)
    generator = DocGenerator(
        client=client,
        max_tokens=int(docgen_cfg.get("max_tokens", 6000)),
    )
    pipeline = DocgenPipeline(
        paths=DocgenPaths(
            gcc_repo_dir=paths_cfg["gcc_repo_dir"],
            file_doc_dir=paths_cfg["file_doc_dir"],
            module_doc_dir=paths_cfg["module_doc_dir"],
        ),
        generator=generator,
        options=DocgenOptions(
            max_code_chars=int(docgen_cfg.get("max_code_chars", 12000)),
            file_doc_batch_size=int(docgen_cfg.get("file_doc_batch_size", 64)),
            same_name_group_batch_size=int(docgen_cfg.get("same_name_group_batch_size", 8)),
            same_name_file_batch_size=int(docgen_cfg.get("same_name_file_batch_size", 24)),
            checkout=not bool(args.no_checkout),
            clean_checkout=bool(args.clean_checkout),
            level=args.level,
        ),
    )

    if args.level in {"all", "file", "same-name"}:
        commits = select_commits(args, rows)
        total = len(commits)
        for index, commit in enumerate(commits, start=1):
            pipeline.run_for_commit(
                commit=commit,
                progress_label=f"{index}/{total}",
            )

    if args.level in {"all", "module"}:
        pipeline.run_module_docs_from_existing_file_docs(progress_label="module")


def select_commits(args: argparse.Namespace, rows: List[Dict[str, str]]) -> List[str]:
    if args.commit.strip():
        return [args.commit.strip()]

    selected_rows = select_rows(args, rows)
    commits: List[str] = []
    for row in selected_rows:
        commit = str(row.get("base_commit") or "").strip()
        if commit:
            commits.append(commit)
    return unique_keep_order(commits)


def select_rows(args: argparse.Namespace, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    by_id = {row.get("instance_id", ""): row for row in rows}
    explicit_ids = unique_keep_order(
        [args.bug_id, *[part.strip() for part in args.bug_ids.split(",") if part.strip()]]
    )
    if explicit_ids:
        missing = [bug_id for bug_id in explicit_ids if bug_id not in by_id]
        if missing:
            raise RuntimeError(f"Unknown bug ids: {', '.join(missing)}")
        return [by_id[bug_id] for bug_id in explicit_ids]
    return rows[: args.limit] if args.limit else rows


if __name__ == "__main__":
    main()
