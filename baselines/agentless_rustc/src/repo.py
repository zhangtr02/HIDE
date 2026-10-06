from __future__ import annotations

from pathlib import Path
from typing import Dict, List


def list_compiler_rs_files(rust_repo_dir: Path) -> List[str]:
    compiler_dir = rust_repo_dir / "compiler"
    if not compiler_dir.is_dir():
        return []
    return sorted(
        path.relative_to(rust_repo_dir).as_posix()
        for path in compiler_dir.rglob("*.rs")
        if path.is_file()
    )


def build_compiler_structure(files: List[str]) -> str:
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
