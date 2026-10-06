from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import load_config
from src.common.json_io import unique_keep_order, write_json
from src.localize.methods import CODE_SUFFIXES, MethodCandidate, extract_method_candidates


PATCH_FILE_RE = re.compile(r"^diff --git a/(?P<old>.*?) b/(?P<new>.*?)$")
HUNK_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_len>\d+))?\s+\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))?\s+@@(?P<header>.*)$"
)
PATCH_COMMIT_RE = re.compile(r"^From\s+([0-9a-fA-F]{40})\s+", re.MULTILINE)


@dataclass(frozen=True)
class PatchHunk:
    file_path: str
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str
    body: List[str]

    @property
    def changed_lines(self) -> List[int]:
        old_line = self.old_start
        deleted: List[int] = []
        anchors: List[int] = []
        touched: List[int] = []
        for line in self.body:
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("-"):
                deleted.append(old_line)
                touched.append(old_line)
                old_line += 1
            elif line.startswith("+"):
                anchors.append(max(self.old_start, old_line - 1))
            else:
                old_line += 1
        if touched:
            return unique_ints(touched)
        if anchors:
            return unique_ints(anchors)
        if self.old_len == 0:
            return [self.old_start]
        return list(range(self.old_start, self.old_start + max(self.old_len, 1)))

    @property
    def deleted_lines(self) -> List[int]:
        old_line = self.old_start
        deleted: List[int] = []
        for line in self.body:
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("-"):
                deleted.append(old_line)
                old_line += 1
            elif line.startswith("+"):
                continue
            else:
                old_line += 1
        return unique_ints(deleted)


@dataclass
class MethodEntry:
    file: str
    item_type: str
    item_name: str
    qualified_name: str
    signature: str
    parent_signature: str
    start_line: int
    end_line: int
    changed_lines: List[int] = field(default_factory=list)
    deleted_lines: List[int] = field(default_factory=list)
    commits: List[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate GCC method-level groundtruth from dataset patches")
    parser.add_argument(
        "--jsonl-path",
        required=True,
        help="Raw GCC dataset JSONL containing patch/base_commit fields",
    )
    parser.add_argument("--bug-id", default="", help="Generate one GCC bug id")
    parser.add_argument("--bug-ids", default="", help="Generate comma-separated GCC bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Generate for the first N rows from the JSONL dataset")
    parser.add_argument("--output-dir", default="", help="Output dir, default method_groundtruth")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    root: Path = cfg["_project_root"]
    paths = cfg["paths"]
    gcc_repo_dir: Path = paths["gcc_repo_dir"]
    jsonl_path = Path(args.jsonl_path).expanduser()
    if not jsonl_path.is_absolute():
        jsonl_path = root / jsonl_path
    if not jsonl_path.exists():
        raise RuntimeError(f"raw GCC JSONL dataset not found: {jsonl_path}")
    output_dir = Path(args.output_dir).resolve() if args.output_dir else paths["method_groundtruth_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    all_rows = read_jsonl(jsonl_path)
    selected_rows = select_rows(args, all_rows)
    generated_rows: List[Dict[str, Any]] = []
    summary: List[Dict[str, Any]] = []

    source_cache: Dict[tuple[str, str], str | None] = {}
    for row in selected_rows:
        result = build_method_groundtruth(row=row, gcc_repo_dir=gcc_repo_dir, source_cache=source_cache)
        bug_id = result["bug_id"]
        if result["methods"]:
            write_json(output_dir / f"{bug_id}.json", result)
            generated_rows.append(row)
            status = "ok"
        else:
            status = "no_method_groundtruth"
        summary.append(
            {
                "bug_id": bug_id,
                "status": status,
                "method_count": len(result["methods"]),
                "code_buggy_file_count": result["stats"]["code_buggy_file_count"],
                "non_code_buggy_file_count": result["stats"]["non_code_buggy_file_count"],
                "missing_base_file_count": result["stats"]["missing_base_file_count"],
                "unresolved_change_count": result["stats"]["unresolved_change_count"],
            }
        )

    write_json(
        output_dir / "summary.json",
        {
            "strategy": "gcc_patch_hunks_to_enclosing_function_like_items",
            "input_bug_count": len(selected_rows),
            "generated_bug_count": len(generated_rows),
            "skipped_bug_count": len(selected_rows) - len(generated_rows),
            "bugs": summary,
        },
    )

    print(
        f"[gcc-method-groundtruth] generated {len(generated_rows)}/{len(selected_rows)} bugs into {output_dir}",
        flush=True,
    )


def build_method_groundtruth(
    *,
    row: Dict[str, Any],
    gcc_repo_dir: Path,
    source_cache: Dict[tuple[str, str], str | None],
) -> Dict[str, Any]:
    bug_id = normalize_bug_id(row.get("instance_id"))
    base_commit = str(row.get("base_commit") or "").strip()
    patch = str(row.get("patch") or "")
    patch_commit = read_patch_commit(patch)
    source_commits = [patch_commit] if patch_commit else []
    buggy_files = read_string_list(row.get("buggy_files"))
    hunk_by_file: Dict[str, List[PatchHunk]] = {}
    for hunk in parse_patch_hunks(patch):
        hunk_by_file.setdefault(hunk.file_path, []).append(hunk)

    methods: Dict[tuple[str, str, int, int], MethodEntry] = {}
    unresolved: List[Dict[str, Any]] = []
    code_buggy_files = 0
    non_code_buggy_files = 0
    missing_base_files = 0
    diff_file_count = 0

    for file_id in buggy_files:
        if Path(file_id).suffix not in CODE_SUFFIXES:
            non_code_buggy_files += 1
            continue
        code_buggy_files += 1
        hunks = hunk_by_file.get(file_id, [])
        if not hunks:
            unresolved.append({"file": file_id, "reason": "no_patch_hunk_for_buggy_file"})
            continue
        diff_file_count += 1
        source = load_source_at_base(gcc_repo_dir=gcc_repo_dir, base_commit=base_commit, file_id=file_id, cache=source_cache)
        if source is None:
            missing_base_files += 1
            unresolved.append({"file": file_id, "reason": "missing_file_at_base_commit"})
            continue
        candidates = extract_method_candidates(file_id=file_id, text=source, max_body_chars=12000)
        changed_lines = unique_ints(line for hunk in hunks for line in hunk.changed_lines)
        deleted_lines = unique_ints(line for hunk in hunks for line in hunk.deleted_lines)
        matched = False
        for method in candidates:
            method_changed = [line for line in changed_lines if method.start_line <= line <= method.end_line]
            method_deleted = [line for line in deleted_lines if method.start_line <= line <= method.end_line]
            if not method_changed and not method_deleted:
                continue
            matched = True
            key = (method.file, method.qualified_name, method.start_line, method.end_line)
            entry = methods.get(key)
            if entry is None:
                entry = method_to_entry(method)
                methods[key] = entry
            entry.changed_lines = unique_ints([*entry.changed_lines, *method_changed, *method_deleted])
            entry.deleted_lines = unique_ints([*entry.deleted_lines, *method_deleted])
            entry.commits = unique_keep_order([*entry.commits, *source_commits])
        if not matched:
            unresolved.append(
                {
                    "file": file_id,
                    "reason": "changed_lines_not_inside_extracted_function",
                    "changed_lines": changed_lines[:50],
                }
            )

    method_entries = sorted(methods.values(), key=lambda item: (item.file, item.start_line, item.qualified_name))
    return {
        "bug_id": bug_id,
        "strategy": "gcc_patch_hunks_to_enclosing_function_like_items",
        "source_commits": source_commits,
        "base_commit": base_commit,
        "methods": [method_entry_to_dict(entry) for entry in method_entries],
        "stats": {
            "buggy_file_count": len(buggy_files),
            "code_buggy_file_count": code_buggy_files,
            "non_code_buggy_file_count": non_code_buggy_files,
            "diff_file_count": diff_file_count,
            "method_count": len(method_entries),
            "missing_base_file_count": missing_base_files,
            "unresolved_change_count": len(unresolved),
        },
    }


def parse_patch_hunks(patch: str) -> List[PatchHunk]:
    hunks: List[PatchHunk] = []
    current_file = ""
    lines = patch.splitlines()
    index = 0
    while index < len(lines):
        file_match = PATCH_FILE_RE.match(lines[index])
        if file_match:
            current_file = file_match.group("old")
            index += 1
            continue
        hunk_match = HUNK_RE.match(lines[index])
        if hunk_match and current_file:
            body: List[str] = []
            index += 1
            while index < len(lines) and not PATCH_FILE_RE.match(lines[index]) and not HUNK_RE.match(lines[index]):
                body.append(lines[index])
                index += 1
            hunks.append(
                PatchHunk(
                    file_path=current_file,
                    old_start=int(hunk_match.group("old_start")),
                    old_len=int(hunk_match.group("old_len") or "1"),
                    new_start=int(hunk_match.group("new_start")),
                    new_len=int(hunk_match.group("new_len") or "1"),
                    header=hunk_match.group("header").strip(),
                    body=body,
                )
            )
            continue
        index += 1
    return hunks


def load_source_at_base(
    *,
    gcc_repo_dir: Path,
    base_commit: str,
    file_id: str,
    cache: Dict[tuple[str, str], str | None],
) -> str | None:
    key = (base_commit, file_id)
    if key in cache:
        return cache[key]
    result = subprocess.run(
        ["git", "-C", str(gcc_repo_dir), "show", f"{base_commit}:{file_id}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    cache[key] = result.stdout if result.returncode == 0 else None
    return cache[key]


def method_to_entry(method: MethodCandidate) -> MethodEntry:
    return MethodEntry(
        file=method.file,
        item_type=method.item_type,
        item_name=method.item_name,
        qualified_name=method.qualified_name,
        signature=method.signature,
        parent_signature=method.parent_signature,
        start_line=method.start_line,
        end_line=method.end_line,
    )


def method_entry_to_dict(entry: MethodEntry) -> Dict[str, Any]:
    return {
        "file": entry.file,
        "item_type": entry.item_type,
        "item_name": entry.item_name,
        "qualified_name": entry.qualified_name,
        "signature": entry.signature,
        "parent_signature": entry.parent_signature,
        "start_line": entry.start_line,
        "end_line": entry.end_line,
        "changed_lines": unique_ints(entry.changed_lines),
        "deleted_lines": unique_ints(entry.deleted_lines),
        "commits": unique_keep_order(entry.commits),
    }


def select_rows(args: argparse.Namespace, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        raw_id = str(row.get("instance_id") or "").strip()
        normalized_id = normalize_bug_id(raw_id)
        if raw_id:
            by_id.setdefault(raw_id, row)
        if normalized_id:
            by_id.setdefault(normalized_id, row)
    requested: List[str] = []
    if args.bug_id.strip():
        requested.append(args.bug_id.strip())
    if args.bug_ids.strip():
        requested.extend(item.strip() for item in args.bug_ids.split(",") if item.strip())
    requested = unique_keep_order(requested)
    if requested:
        missing = [bug_id for bug_id in requested if bug_id not in by_id]
        if missing:
            raise RuntimeError(f"bug_id not found in GCC JSONL dataset: {', '.join(missing)}")
        return [by_id[bug_id] for bug_id in requested]
    return rows[: args.limit] if args.limit else rows


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise RuntimeError(f"Expected JSON object in {path}")
            rows.append(obj)
    return rows


def read_patch_commit(patch: str) -> str:
    match = PATCH_COMMIT_RE.search(patch)
    return match.group(1) if match else ""


def normalize_bug_id(value: Any) -> str:
    text = str(value or "").strip()
    return text[4:] if text.startswith("gcc-") else text


def read_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return unique_keep_order([str(item or "").strip() for item in value if str(item or "").strip()])
    if isinstance(value, str):
        return unique_keep_order([item.strip() for item in value.split(",") if item.strip()])
    return []


def unique_ints(values: Iterable[int]) -> List[int]:
    out: List[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            number = int(value)
        except Exception:
            continue
        if number not in seen:
            seen.add(number)
            out.append(number)
    return sorted(out)


if __name__ == "__main__":
    main()
