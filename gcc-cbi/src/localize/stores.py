from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.json_io import load_json_if_exists
from src.localize.repo import RepoEnumerator, unique


class DocStore:
    def __init__(
        self,
        *,
        module_doc_dir: Path,
        file_doc_dir: Path,
        gcc_repo_dir: Path,
    ) -> None:
        self.module_doc_dir = module_doc_dir
        self.file_doc_dir = file_doc_dir
        self.gcc_repo_dir = gcc_repo_dir

    def load_module_docs(self, module_ids: Iterable[str] | None = None) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        ids = unique(module_ids or RepoEnumerator(gcc_repo_dir=self.gcc_repo_dir).enumerate_modules())
        for module_id in ids:
            doc = load_json_if_exists(self.module_doc_dir / Path(module_id).with_suffix(".json"))
            docs.append(doc if doc else {"id": module_id, "responsibility": ""})
        return docs

    def load_file_docs(self, file_ids: Iterable[str]) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        for file_id in unique(file_ids):
            doc = load_json_if_exists(self.file_doc_dir / f"{file_id}.json")
            if not doc:
                doc = load_json_if_exists(self.file_doc_dir / Path(file_id).with_suffix(".json"))
            docs.append(doc if doc else {"id": file_id, "responsibility": "", "same_name_distinction": ""})
        return docs
