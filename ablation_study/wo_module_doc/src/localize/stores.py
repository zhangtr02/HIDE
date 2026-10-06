from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.json_io import load_json_if_exists
from src.localize.repo import unique


class DocStore:
    def __init__(
        self,
        *,
        crate_doc_dir: Path,
        module_doc_dir: Path,
        file_doc_dir: Path,
        rust_repo_dir: Path,
    ) -> None:
        self.crate_doc_dir = crate_doc_dir
        self.module_doc_dir = module_doc_dir
        self.file_doc_dir = file_doc_dir
        self.rust_repo_dir = rust_repo_dir

    def load_crate_docs(self, crate_ids: Iterable[str]) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        for crate_id in unique(crate_ids):
            doc = load_json_if_exists(self.crate_doc_dir / f"{crate_id}.json")
            docs.append(doc if doc else {"id": crate_id, "responsibility": ""})
        return docs

    def load_module_docs(self, module_ids: Iterable[str]) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        for module_id in unique(module_ids):
            crate_id, sep, module = module_id.partition("::")
            if not sep:
                docs.append({"id": module_id, "responsibility": ""})
                continue
            doc = load_json_if_exists(self.module_doc_dir / crate_id / f"{module.replace('::', '/')}.json")
            docs.append(doc if doc else {"id": module_id, "responsibility": ""})
        return docs

    def load_file_docs(self, file_ids: Iterable[str]) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        for file_id in unique(file_ids):
            doc = load_json_if_exists(self.file_doc_dir / Path(file_id).with_suffix(".json"))
            docs.append(doc if doc else {"id": file_id, "responsibility": "", "same_name_distinction": ""})
        return docs
