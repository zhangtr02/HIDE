from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from src.common.gcc_modules import enumerate_files_for_module, enumerate_virtual_modules, unique


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
    ".texi",
    ".in",
    ".urls",
}


class RepoEnumerator:
    """Enumerates GCC candidates from the currently checked-out GCC repository."""

    def __init__(self, *, gcc_repo_dir: Path) -> None:
        self.gcc_repo_dir = gcc_repo_dir
        self.gcc_dir = gcc_repo_dir / "gcc"

    def enumerate_modules(self) -> List[str]:
        return enumerate_virtual_modules(self.gcc_repo_dir, source_suffixes=SOURCE_SUFFIXES)

    def enumerate_files_for_modules(self, module_ids: Iterable[str]) -> List[str]:
        files: List[str] = []
        for module_id in unique(module_ids):
            files.extend(enumerate_files_for_module(self.gcc_repo_dir, module_id, source_suffixes=SOURCE_SUFFIXES))
        return unique(files)

    def enumerate_root_files(self) -> List[str]:
        return enumerate_files_for_module(self.gcc_repo_dir, "gcc/root", source_suffixes=SOURCE_SUFFIXES)
