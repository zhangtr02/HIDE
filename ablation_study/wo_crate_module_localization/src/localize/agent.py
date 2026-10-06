from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.common.llm import LLMClient, chat_json_with_validation
from src.localize.model import Candidate, CandidateResult
from src.localize.prompts import build_file_localization_messages
from src.localize.schemas import parse_candidate_result


class LocalizationAgent:
    def __init__(self, *, client: LLMClient, candidate_max_tokens: int) -> None:
        self.client = client
        self.candidate_max_tokens = candidate_max_tokens

    def run_file_localization(
        self,
        *,
        issue: Dict[str, Any],
        reproducer: Dict[str, Any],
        evidence: Dict[str, Any],
        file_docs: List[Dict[str, Any]],
        available_files: List[str],
        max_candidates: int,
    ) -> CandidateResult:
        allowed = clean_ids(available_files)
        if not allowed:
            return CandidateResult()
        if max_candidates > 0 and len(allowed) <= max_candidates:
            return CandidateResult(
                [Candidate(id=item, reason="All files fit within the candidate budget.") for item in allowed]
            )
        return chat_json_with_validation(
            self.client,
            build_file_localization_messages(
                issue=issue,
                reproducer=reproducer,
                evidence=evidence,
                file_docs=file_docs,
                available_files=allowed,
                max_candidates=max_candidates,
            ),
            lambda raw: require_candidates(
                parse_candidate_result(raw, allowed_ids=allowed, max_candidates=max_candidates),
                expected_count=expected_count(allowed, max_candidates),
                allowed_ids=allowed,
            ),
            stage="global_file_localization",
            max_tokens=self.candidate_max_tokens,
            schema_name="global file candidates",
            retries=3,
        )


def clean_ids(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def expected_count(allowed: List[str], limit: int) -> int:
    return min(limit, len(allowed)) if limit > 0 else len(allowed)


def require_candidates(
    result: CandidateResult,
    *,
    expected_count: int,
    allowed_ids: List[str],
) -> CandidateResult:
    if not result.candidates:
        raise ValueError("global file localization returned no valid candidates from the allowed set")
    candidates = list(result.candidates[:expected_count])
    seen = {candidate.id for candidate in candidates}
    for candidate_id in allowed_ids:
        if len(candidates) >= expected_count:
            break
        if candidate_id in seen:
            continue
        candidates.append(
            Candidate(
                id=candidate_id,
                reason="Padding fallback from remaining global file candidates.",
            )
        )
        seen.add(candidate_id)
    if len(candidates) < expected_count:
        raise ValueError(
            f"global file localization returned {len(candidates)} candidates; expected {expected_count}"
        )
    return CandidateResult(candidates=candidates)
