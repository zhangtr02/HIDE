from __future__ import annotations

from pathlib import Path

from src.common.gcc_modules import enumerate_files_for_module, enumerate_virtual_modules, module_abs_path
from src.docgen.model import GCCFile, GCCModule, GCCTree


SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".ac",
    ".bnf",
    ".h",
    ".md",
    ".def",
    ".opt",
    ".mod",
    ".inc",
    ".in",
    ".awk",
    ".pd",
    ".py",
    ".sh",
    ".texi",
    ".urls",
    ".y",
}

SKIP_DIRS = {
    ".git",
    "__pycache__",
}

SKIP_TOP_LEVEL_MODULES = {
    "testsuite",
}


def scan_gcc_modules(gcc_repo_dir: Path) -> GCCTree:
    gcc_dir = gcc_repo_dir / "gcc"
    if not gcc_dir.exists():
        raise FileNotFoundError(f"GCC source directory not found: {gcc_dir}")

    tree = GCCTree()
    for module_id in enumerate_virtual_modules(gcc_repo_dir, source_suffixes=SOURCE_SUFFIXES):
        module = GCCModule(id=module_id, abs_path=module_abs_path(gcc_repo_dir, module_id))
        for rel in enumerate_files_for_module(gcc_repo_dir, module_id, source_suffixes=SOURCE_SUFFIXES):
            file_path = gcc_repo_dir / rel
            module.files.append(
                GCCFile(
                    id=rel,
                    abs_path=file_path,
                    module_id=module_id,
                    basename=file_path.name,
                )
            )
        if module.files:
            tree.modules[module_id] = module
    return tree
