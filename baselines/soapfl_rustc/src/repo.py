from __future__ import annotations

import re
from pathlib import Path
from typing import List


def list_compiler_rs_files(rust_repo_dir: Path) -> List[str]:
    compiler_dir = rust_repo_dir / "compiler"
    if not compiler_dir.is_dir():
        return []
    return sorted(
        path.relative_to(rust_repo_dir).as_posix()
        for path in compiler_dir.rglob("*.rs")
        if path.is_file()
    )


def read_file_context(rust_repo_dir: Path, file_id: str, *, max_chars: int) -> str:
    path = rust_repo_dir / file_id
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    return f"=== {file_id} ===\n{text}"


def fallback_file_summary(rust_repo_dir: Path, file_id: str, *, max_chars: int = 10000) -> str:
    path = rust_repo_dir / file_id
    if not path.is_file():
        return f"Rust compiler source file {file_id}."
    text = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    items = unique_names(
        re.findall(r"(?:pub\s+)?(?:mod|fn|struct|enum|trait|impl)\s+([A-Za-z_][A-Za-z0-9_]*)", text)
    )
    imports = unique_names(re.findall(r"^\s*use\s+([A-Za-z0-9_:]+)", text, flags=re.MULTILINE))
    parts = [f"Rust compiler source file {file_id}."]
    if items:
        parts.append("Defines or implements: " + ", ".join(items[:10]) + ".")
    if imports:
        parts.append("Imports include: " + ", ".join(imports[:6]) + ".")
    return " ".join(parts)


def unique_names(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
