from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import load_config
from src.common.json_io import load_json, unique_keep_order, write_json
from src.localize.methods import MethodCandidate, extract_method_candidates


PATCH_FILE_RE = re.compile(r"^diff --git a/(?P<old>.*?) b/(?P<new>.*?)$")
HUNK_RE = re.compile(
    r"^@@\s+-(?P<old_start>\d+)(?:,(?P<old_len>\d+))?\s+\+(?P<new_start>\d+)(?:,(?P<new_len>\d+))?\s+@@(?P<header>.*)$"
)


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
        for line in self.body:
            if line.startswith("---") or line.startswith("+++"):
                continue
            if line.startswith("-"):
                deleted.append(old_line)
                old_line += 1
            elif line.startswith("+"):
                anchors.append(max(self.old_start, old_line - 1))
            else:
                old_line += 1
        if deleted:
            return unique_ints(deleted)
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
    parser = argparse.ArgumentParser(description="Generate rustc method-level groundtruth from PR commit diffs")
    parser.add_argument("--bug-id", default="", help="Generate one rustc bug id")
    parser.add_argument("--bug-ids", default="", help="Generate comma-separated rustc bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Generate for the first N rows from rustcbugs.csv")
    parser.add_argument("--output-dir", default="", help="Output dir, default method_groundtruth")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    paths = cfg["paths"]
    output_dir = Path(args.output_dir).resolve() if args.output_dir else paths["method_groundtruth_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)

    bug_ids = select_bug_ids(args, read_bug_ids(paths["csv_file"]))
    summary: List[Dict[str, Any]] = []
    generated = 0
    source_cache: Dict[tuple[str, str], str | None] = {}
    for bug_id in bug_ids:
        groundtruth_path = paths["groundtruth_dir"] / f"{bug_id}.json"
        if not groundtruth_path.exists():
            summary.append({"bug_id": bug_id, "status": "missing_groundtruth", "method_count": 0})
            continue
        result = build_method_groundtruth(
            bug_id=bug_id,
            groundtruth=load_json(groundtruth_path),
            rust_repo_dir=paths["rust_repo_dir"],
            source_cache=source_cache,
        )
        if result["methods"]:
            write_json(output_dir / f"{bug_id}.json", result)
            generated += 1
            status = "ok"
        else:
            status = "no_method_groundtruth"
        summary.append({"bug_id": bug_id, "status": status, "method_count": len(result["methods"]), **result["stats"]})

    write_json(
        output_dir / "summary.json",
        {
            "strategy": "pr_commit_diff_hunks_to_enclosing_function_like_rust_items",
            "input_bug_count": len(bug_ids),
            "generated_bug_count": generated,
            "skipped_bug_count": len(bug_ids) - generated,
            "bugs": summary,
        },
    )
    print(f"[rustc-method-groundtruth] generated {generated}/{len(bug_ids)} bugs into {output_dir}", flush=True)


def build_method_groundtruth(
    *,
    bug_id: str,
    groundtruth: Dict[str, Any],
    rust_repo_dir: Path,
    source_cache: Dict[tuple[str, str], str | None],
) -> Dict[str, Any]:
    compiler_files = [
        file_id for file_id in read_string_list(groundtruth.get("compiler_rs_files"))
        if file_id.startswith("compiler/") and file_id.endswith(".rs")
    ]
    compiler_file_set = set(compiler_files)
    commits = [
        str(item.get("sha") or "").strip()
        for item in groundtruth.get("commits", [])
        if isinstance(item, dict)
        and any(file_id in compiler_file_set for file_id in read_string_list(item.get("files")))
    ]
    commits = unique_keep_order(commits)

    methods: Dict[tuple[str, str, int, int], MethodEntry] = {}
    unresolved: List[Dict[str, Any]] = []
    diff_file_count = 0

    for commit in commits:
        patch = git_show_patch(rust_repo_dir, commit, compiler_files)
        hunk_by_file: Dict[str, List[PatchHunk]] = {}
        for hunk in parse_patch_hunks(patch):
            if hunk.file_path in compiler_file_set:
                hunk_by_file.setdefault(hunk.file_path, []).append(hunk)
        for file_id, hunks in hunk_by_file.items():
            diff_file_count += 1
            source = load_source_before_commit(rust_repo_dir, commit=commit, file_id=file_id, cache=source_cache)
            if source is None:
                unresolved.append({"file": file_id, "commit": commit, "reason": "missing_preimage_file"})
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
                entry.commits = unique_keep_order([*entry.commits, commit])
            if not matched:
                unresolved.append(
                    {
                        "file": file_id,
                        "commit": commit,
                        "reason": "changed_lines_not_inside_extracted_function_like_item",
                        "changed_lines": changed_lines[:50],
                    }
                )

    method_entries = sorted(methods.values(), key=lambda item: (item.file, item.start_line, item.qualified_name))
    return {
        "bug_id": bug_id,
        "strategy": "pr_commit_diff_hunks_to_enclosing_function_like_rust_items",
        "source_commits": commits,
        "methods": [method_entry_to_dict(entry) for entry in method_entries],
        "stats": {
            "compiler_rs_file_count": len(compiler_files),
            "diff_file_count": diff_file_count,
            "method_count": len(method_entries),
            "unresolved_change_count": len(unresolved),
        },
        "unresolved": unresolved[:50],
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
            while index < len(lines) and not HUNK_RE.match(lines[index]) and not PATCH_FILE_RE.match(lines[index]):
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


def git_show_patch(rust_repo_dir: Path, commit: str, files: List[str]) -> str:
    if not commit or not files:
        return ""
    result = subprocess.run(
        ["git", "-C", str(rust_repo_dir), "show", "--format=", "--find-renames", "--unified=0", commit, "--", *files],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def load_source_before_commit(
    rust_repo_dir: Path,
    *,
    commit: str,
    file_id: str,
    cache: Dict[tuple[str, str], str | None],
) -> str | None:
    key = (commit, file_id)
    if key in cache:
        return cache[key]
    for parent_ref in (f"{commit}^", f"{commit}~1"):
        result = subprocess.run(
            ["git", "-C", str(rust_repo_dir), "show", f"{parent_ref}:{file_id}"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode == 0:
            cache[key] = result.stdout
            return result.stdout
    cache[key] = None
    return None


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
        "changed_lines": entry.changed_lines,
        "deleted_lines": entry.deleted_lines,
        "commits": entry.commits,
    }


def read_bug_ids(path: Path) -> List[str]:
    bug_ids: List[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        bug_ids.append(line.split(",", 1)[0].strip())
    return unique_keep_order(bug_ids)


def select_bug_ids(args: argparse.Namespace, bug_ids: List[str]) -> List[str]:
    requested: List[str] = []
    if args.bug_id.strip():
        requested.append(args.bug_id.strip())
    if args.bug_ids.strip():
        requested.extend(item.strip() for item in args.bug_ids.split(",") if item.strip())
    requested = unique_keep_order(requested)
    if requested:
        missing = [bug_id for bug_id in requested if bug_id not in set(bug_ids)]
        if missing:
            raise RuntimeError(f"bug_id not found in rustcbugs.csv: {', '.join(missing)}")
        return requested
    return bug_ids[: args.limit] if args.limit else bug_ids


def read_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def unique_ints(items: Iterable[int]) -> List[int]:
    out: List[int] = []
    seen: set[int] = set()
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


if __name__ == "__main__":
    main()
