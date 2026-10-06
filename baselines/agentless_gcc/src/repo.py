from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List


SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".inc",
    ".def",
    ".opt",
    ".md",
    ".pd",
}
SKIP_DIRS = {".git", "__pycache__"}
SKIP_TOP_LEVEL_MODULES = {"testsuite"}


def list_gcc_source_files(gcc_repo_dir: Path) -> List[str]:
    gcc_dir = gcc_repo_dir / "gcc"
    if not gcc_dir.is_dir():
        return []
    files: List[str] = []
    for path in sorted(gcc_dir.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(gcc_repo_dir).as_posix()
        if rel.startswith("gcc/testsuite/"):
            continue
        files.append(rel)
    return unique(files)


def build_gcc_structure(files: Iterable[str]) -> str:
    root: Dict[str, dict] = {}
    for file_path in files:
        node = root
        for part in file_path.split("/"):
            node = node.setdefault(part, {})
    return format_tree(root).rstrip()


def format_tree(node: Dict[str, dict], indent: int = 0) -> str:
    lines: List[str] = []
    for name in sorted(node):
        child = node[name]
        if child:
            lines.append(" " * indent + f"{name}/")
            lines.append(format_tree(child, indent + 4))
        else:
            lines.append(" " * indent + name)
    return "\n".join(line for line in lines if line)


def unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
