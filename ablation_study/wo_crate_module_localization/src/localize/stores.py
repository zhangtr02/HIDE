from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.json_io import load_json_if_exists
from src.localize.repo import unique


class DocStore:
    def __init__(self, *, file_doc_dir: Path) -> None:
        self.file_doc_dir = file_doc_dir

    def load_file_docs(self, file_ids: Iterable[str]) -> List[Dict[str, Any]]:
        docs: List[Dict[str, Any]] = []
        for file_id in unique(file_ids):
            doc = load_json_if_exists(self.file_doc_dir / Path(file_id).with_suffix(".json"))
            docs.append(doc if doc else {"id": file_id, "responsibility": "", "same_name_distinction": ""})
        return docs
