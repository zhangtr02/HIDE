from __future__ import annotations

from pathlib import Path

from src.docgen.model import RustCrate, RustFile, RustModule, RustTree
from src.localize.repo import RepoEnumerator, unique


SOURCE_SUFFIXES = {".rs"}

SKIP_DIRS = {
    ".git",
    "__pycache__",
}

def scan_rustc_tree(rust_repo_dir: Path) -> RustTree:
    compiler_dir = rust_repo_dir / "compiler"
    if not compiler_dir.exists():
        raise FileNotFoundError(f"rustc compiler source directory not found: {compiler_dir}")

    enumerator = RepoEnumerator(rust_repo_dir=rust_repo_dir)
    tree = RustTree()
    for crate_id in enumerator.enumerate_crates():
        crate_dir = compiler_dir / crate_id
        src_dir = crate_dir / "src"
        rust_crate = RustCrate(id=crate_id, abs_path=crate_dir)
        root_files = enumerator.enumerate_root_files([crate_id])
        root_module_id = f"{crate_id}::__root__"
        if root_files:
            root_module = RustModule(id=root_module_id, abs_path=src_dir, crate_id=crate_id, is_root=True)
            for file_id in root_files:
                item = rust_file(rust_repo_dir, file_id, root_module_id)
                root_module.files.append(item)
                rust_crate.files.append(item)
            tree.modules[root_module_id] = root_module

        for module_id in enumerator.enumerate_modules([crate_id]):
            files = enumerator.enumerate_files_for_modules([module_id])
            if not files:
                continue
            module_path = module_abs_path(rust_repo_dir, module_id)
            module = RustModule(id=module_id, abs_path=module_path, crate_id=crate_id)
            for file_id in files:
                item = rust_file(rust_repo_dir, file_id, module_id)
                module.files.append(item)
                rust_crate.files.append(item)
            tree.modules[module_id] = module
            rust_crate.modules.append(module_id)
        rust_crate.files = unique_rust_files(rust_crate.files)
        tree.crates[crate_id] = rust_crate
    return tree


def rust_file(rust_repo_dir: Path, file_id: str, module_id: str) -> RustFile:
    abs_path = rust_repo_dir / file_id
    return RustFile(id=file_id, abs_path=abs_path, module_id=module_id, basename=abs_path.name)


def module_abs_path(rust_repo_dir: Path, module_id: str) -> Path:
    crate_id, _, module = module_id.partition("::")
    if module == "__root__":
        return rust_repo_dir / "compiler" / crate_id / "src"
    src_dir = rust_repo_dir / "compiler" / crate_id / "src"
    rel = Path(module.replace("::", "/"))
    module_file = (src_dir / rel).with_suffix(".rs")
    if module_file.exists():
        return module_file
    return src_dir / rel


def unique_rust_files(files: list[RustFile]) -> list[RustFile]:
    by_id = {item.id: item for item in files}
    return [by_id[file_id] for file_id in unique(by_id.keys())]
