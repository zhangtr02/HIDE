from __future__ import annotations

from pathlib import Path


def file_doc_path(file_doc_dir: Path, file_id: str) -> Path:
    return file_doc_dir / f"{file_id}.json"


def module_doc_path(module_doc_dir: Path, module_id: str) -> Path:
    return module_doc_dir / Path(module_id).with_suffix(".json")
