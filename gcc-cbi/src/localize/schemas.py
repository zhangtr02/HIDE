from __future__ import annotations

from typing import Any, Iterable, List

from src.localize.model import Candidate, CandidateResult, RankedMethod, RankedMethodResult


def parse_candidate_result(raw: Any, *, allowed_ids: Iterable[str], max_candidates: int) -> CandidateResult:
    allowed_order = [str(item or "").strip() for item in allowed_ids if str(item or "").strip()]
    allowed = set(allowed_order)
    obj = raw if isinstance(raw, dict) else {}
    items = obj.get("candidates") if isinstance(obj.get("candidates"), list) else []
    candidates: List[Candidate] = []
    seen: set[str] = set()

    for item in items:
        record = item if isinstance(item, dict) else {}
        item_id = str(record.get("id") or "").strip()
        reason = str(record.get("reason") or "").strip()
        if not item_id or not reason:
            continue
        if item_id not in allowed or item_id in seen:
            continue
        seen.add(item_id)
        candidates.append(Candidate(id=item_id, reason=reason))
        if max_candidates > 0 and len(candidates) >= max_candidates:
            break
    return CandidateResult(candidates=candidates)


def parse_ranked_method_result(
    raw: Any,
    *,
    candidate_method_ids: Iterable[str],
    max_methods: int,
) -> RankedMethodResult:
    allowed_order = [str(item or "").strip() for item in candidate_method_ids if str(item or "").strip()]
    allowed = set(allowed_order)
    obj = raw if isinstance(raw, dict) else {}
    items = obj.get("ranked_methods") if isinstance(obj.get("ranked_methods"), list) else []
    ranked: List[RankedMethod] = []
    seen: set[str] = set()
    next_rank = 1

    for item in items:
        record = item if isinstance(item, dict) else {}
        method_id = str(record.get("id") or "").strip()
        reason = str(record.get("reason") or "").strip()
        if not method_id or method_id not in allowed or method_id in seen or not reason:
            continue
        rank = positive_int(record.get("rank"), fallback=next_rank)
        ranked.append(RankedMethod(id=method_id, rank=rank, reason=reason))
        seen.add(method_id)
        next_rank = max(next_rank, rank + 1)
        if max_methods > 0 and len(ranked) >= max_methods:
            break

    normalized = [
        RankedMethod(id=item.id, rank=index, reason=item.reason)
        for index, item in enumerate(sorted(ranked, key=lambda item: item.rank), start=1)
    ]
    return RankedMethodResult(ranked_methods=normalized)


def positive_int(value: Any, *, fallback: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return fallback
    return parsed if parsed > 0 else fallback
