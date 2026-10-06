from __future__ import annotations

from pathlib import Path
from typing import Iterable, List


class RepoEnumerator:
    """Enumerates rustc candidates from the current rust checkout."""

    def __init__(self, *, rust_repo_dir: Path) -> None:
        self.rust_repo_dir = rust_repo_dir
        self.compiler_dir = rust_repo_dir / "compiler"

    def enumerate_crates(self) -> List[str]:
        if not self.compiler_dir.is_dir():
            return []
        crates: List[str] = []
        for path in sorted(self.compiler_dir.iterdir()):
            if path.is_dir() and (path / "src").is_dir():
                crates.append(path.name)
        return crates

    def enumerate_modules(self, crate_ids: Iterable[str]) -> List[str]:
        modules: List[str] = []
        for crate_id in unique(crate_ids):
            src_dir = self.compiler_dir / crate_id / "src"
            if not src_dir.is_dir():
                continue
            names: set[str] = set()
            for child in sorted(src_dir.iterdir()):
                if child.is_dir() and has_rs_file(child):
                    names.add(child.name)
                elif child.is_file() and child.suffix == ".rs" and child.stem not in {"lib", "main", "mod"}:
                    names.add(child.stem)
            modules.extend(f"{crate_id}::{name}" for name in sorted(names))
        return unique(modules)

    def enumerate_root_files(self, crate_ids: Iterable[str]) -> List[str]:
        files: List[str] = []
        for crate_id in unique(crate_ids):
            src_dir = self.compiler_dir / crate_id / "src"
            if not src_dir.is_dir():
                continue
            for path in sorted(src_dir.glob("*.rs")):
                files.append(path.relative_to(self.rust_repo_dir).as_posix())
        return unique(files)

    def enumerate_files_for_modules(self, module_ids: Iterable[str]) -> List[str]:
        files: List[str] = []
        for module_id in unique(module_ids):
            crate_id, sep, module = module_id.partition("::")
            if not sep:
                continue
            src_dir = self.compiler_dir / crate_id / "src"
            rel = Path(module.replace("::", "/"))
            module_file = (src_dir / rel).with_suffix(".rs")
            module_dir = src_dir / rel
            if module_file.is_file():
                files.append(module_file.relative_to(self.rust_repo_dir).as_posix())
            if module_dir.is_dir():
                for path in sorted(module_dir.rglob("*.rs")):
                    files.append(path.relative_to(self.rust_repo_dir).as_posix())
        return unique(files)


def has_rs_file(path: Path) -> bool:
    return any(child.is_file() and child.suffix == ".rs" for child in path.rglob("*.rs"))


def unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
