from __future__ import annotations

from pathlib import Path


def file_doc_path(file_doc_dir: Path, file_id: str) -> Path:
    return file_doc_dir / Path(file_id).with_suffix(".json")


def module_doc_path(module_doc_dir: Path, module_id: str) -> Path:
    crate_id, sep, module = module_id.partition("::")
    if not sep:
        return module_doc_dir / f"{crate_id}.json"
    return module_doc_dir / crate_id / Path(module).with_suffix(".json")


def crate_doc_path(crate_doc_dir: Path, crate_id: str) -> Path:
    return crate_doc_dir / f"{crate_id}.json"
