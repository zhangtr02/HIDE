from __future__ import annotations

from typing import Any, Dict, List

from src.common.llm import LLMClient, chat_json_with_validation
from src.docgen.prompts.file_prompt import build_file_doc_messages
from src.docgen.prompts.module_prompt import build_module_doc_messages
from src.docgen.prompts.same_name_prompt import build_same_name_messages
from src.docgen.schemas.doc_schema import validate_file_docs, validate_module_docs, validate_same_name_updates


class DocGenerator:
    def __init__(self, *, client: LLMClient, max_tokens: int) -> None:
        self.client = client
        self.max_tokens = max_tokens

    def generate_file_docs(self, payload: Dict[str, Any], *, target_ids: List[str]) -> List[Dict[str, str]]:
        return chat_json_with_validation(
            self.client,
            build_file_doc_messages(payload),
            lambda raw: validate_file_docs(raw, target_ids=target_ids, require_empty_same_name=True),
            max_tokens=self.max_tokens,
            schema_name="file doc schema",
            retries=1,
        )

    def generate_module_docs(self, payload: Dict[str, Any], *, module_ids: List[str]) -> List[Dict[str, str]]:
        return chat_json_with_validation(
            self.client,
            build_module_doc_messages(payload),
            lambda raw: validate_module_docs(raw, module_ids=module_ids),
            max_tokens=self.max_tokens,
            schema_name="module doc schema",
            retries=1,
        )

    def generate_same_name_differentiation(
        self,
        payload: Dict[str, Any],
        *,
        file_ids: List[str],
    ) -> List[Dict[str, str]]:
        return chat_json_with_validation(
            self.client,
            build_same_name_messages(payload),
            lambda raw: validate_same_name_updates(raw, file_ids=file_ids),
            max_tokens=self.max_tokens,
            schema_name="same-name differentiation schema",
            retries=1,
        )
