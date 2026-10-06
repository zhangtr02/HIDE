from __future__ import annotations

from pathlib import Path
from typing import Iterable, List


class RepoEnumerator:
    """Enumerates every rustc source file from the current checkout."""

    def __init__(self, *, rust_repo_dir: Path) -> None:
        self.rust_repo_dir = rust_repo_dir
        self.compiler_dir = rust_repo_dir / "compiler"

    def enumerate_files(self) -> List[str]:
        if not self.compiler_dir.is_dir():
            return []
        return [
            path.relative_to(self.rust_repo_dir).as_posix()
            for path in sorted(self.compiler_dir.rglob("*.rs"))
            if path.is_file()
        ]


def unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
