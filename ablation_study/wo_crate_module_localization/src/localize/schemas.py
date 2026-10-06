from __future__ import annotations

from typing import Any, Iterable, List

from src.localize.model import Candidate, CandidateResult


def parse_candidate_result(raw: Any, *, allowed_ids: Iterable[str], max_candidates: int) -> CandidateResult:
    allowed = {str(item or "").strip() for item in allowed_ids if str(item or "").strip()}
    obj = raw if isinstance(raw, dict) else {}
    items = obj.get("candidates") if isinstance(obj.get("candidates"), list) else []
    candidates: List[Candidate] = []
    seen: set[str] = set()

    for item in items:
        if isinstance(item, str):
            item_id = item.strip()
            reason = "Selected by the model without an explicit reason."
        else:
            record = item if isinstance(item, dict) else {}
            item_id = first_text(record, ["id", "candidate_id", "path", "file"])
            reason = first_text(record, ["reason", "rationale", "explanation", "why"])
            if item_id and not reason:
                reason = "Selected by the model without an explicit reason."
        if not item_id or item_id not in allowed or item_id in seen:
            continue
        seen.add(item_id)
        candidates.append(Candidate(id=item_id, reason=reason))
        if max_candidates > 0 and len(candidates) >= max_candidates:
            break
    return CandidateResult(candidates=candidates)


def first_text(record: dict[str, Any], keys: List[str]) -> str:
    for key in keys:
        value = record.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
