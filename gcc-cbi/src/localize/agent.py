from __future__ import annotations

from typing import Any, Dict, Iterable, List

from src.common.llm import LLMClient, chat_json_with_validation
from src.localize.methods import MethodCandidate
from src.localize.model import Candidate, CandidateResult, RankedMethod, RankedMethodResult
from src.localize.prompts import (
    build_stage1_messages,
    build_stage2_file_messages,
    build_stage4_method_file_messages,
    build_stage4_method_global_messages,
)
from src.localize.schemas import parse_candidate_result, parse_ranked_method_result


class LocalizationAgent:
    def __init__(self, *, client: LLMClient, candidate_max_tokens: int, code_max_tokens: int) -> None:
        self.client = client
        self.candidate_max_tokens = candidate_max_tokens
        self.code_max_tokens = code_max_tokens

    def run_stage1(
        self,
        *,
        issue: Dict[str, Any],
        reproducer: Dict[str, Any],
        execution: Dict[str, Any],
        log: Dict[str, Any],
        pass_trace: Dict[str, Any],
        module_docs: List[Dict[str, Any]],
        available_modules: List[str],
        max_candidates: int,
    ) -> CandidateResult:
        allowed = clean_ids(available_modules)
        if not allowed:
            return CandidateResult()
        return chat_json_with_validation(
            self.client,
            build_stage1_messages(
                issue=issue,
                reproducer=reproducer,
                execution=execution,
                log=log,
                pass_trace=pass_trace,
                module_docs=module_docs,
                available_modules=allowed,
                max_candidates=max_candidates,
            ),
            lambda raw: require_candidates(
                parse_candidate_result(raw, allowed_ids=allowed, max_candidates=max_candidates),
                stage="stage1",
                max_count=max_candidates,
                require_non_empty=True,
            ),
            stage="stage1_module",
            max_tokens=self.candidate_max_tokens,
            schema_name="stage1 module candidates",
            retries=1,
        )

    def run_stage2_file_screening(
        self,
        *,
        issue: Dict[str, Any],
        reproducer: Dict[str, Any],
        execution: Dict[str, Any],
        log: Dict[str, Any],
        pass_trace: Dict[str, Any],
        stage1_result: CandidateResult,
        candidate_modules: List[str],
        file_docs: List[Dict[str, Any]],
        available_files: List[str],
        max_candidates: int,
        screening_context: Dict[str, Any] | None = None,
    ) -> CandidateResult:
        allowed = clean_ids(available_files)
        if not allowed:
            return CandidateResult()
        if max_candidates > 0 and len(allowed) <= max_candidates:
            return CandidateResult(
                [
                    Candidate(
                        id=item,
                        reason="All files in this Stage2 screening group fit within the candidate budget.",
                    )
                    for item in allowed
                ]
            )
        return chat_json_with_validation(
            self.client,
            build_stage2_file_messages(
                issue=issue,
                reproducer=reproducer,
                execution=execution,
                log=log,
                pass_trace=pass_trace,
                stage1_result=stage1_result.to_dict(),
                candidate_modules=candidate_modules,
                file_docs=file_docs,
                available_files=allowed,
                max_candidates=max_candidates,
                screening_context=screening_context,
            ),
            lambda raw: require_candidates(
                parse_candidate_result(raw, allowed_ids=allowed, max_candidates=max_candidates),
                stage="stage2 file screening",
                max_count=max_candidates,
                require_non_empty=True,
            ),
            stage="stage2_file",
            max_tokens=self.candidate_max_tokens,
            schema_name="stage2 file candidates",
            retries=1,
        )

    def run_stage4_method_file_selection(
        self,
        *,
        issue: Dict[str, Any],
        reproducer: Dict[str, Any],
        execution: Dict[str, Any],
        log: Dict[str, Any],
        pass_trace: Dict[str, Any],
        stage1_result: CandidateResult,
        stage2_file_selection: CandidateResult,
        file_id: str,
        methods: List[MethodCandidate],
        max_candidates: int,
    ) -> CandidateResult:
        allowed = clean_ids([method.id for method in methods])
        if not allowed:
            return CandidateResult()
        if max_candidates > 0 and len(allowed) <= max_candidates:
            return CandidateResult(
                candidates=[
                    Candidate(id=method_id, reason="All methods in this file fit within the candidate budget.")
                    for method_id in allowed
                ]
            )
        return chat_json_with_validation(
            self.client,
            build_stage4_method_file_messages(
                issue=issue,
                reproducer=reproducer,
                execution=execution,
                log=log,
                pass_trace=pass_trace,
                stage1_result=stage1_result.to_dict(),
                stage2_file_selection=stage2_file_selection.to_dict(),
                file_id=file_id,
                method_candidates=[method.to_prompt_dict(include_body=False) for method in methods],
                max_candidates=max_candidates,
            ),
            lambda raw: require_candidates(
                parse_candidate_result(raw, allowed_ids=allowed, max_candidates=max_candidates),
                stage="stage4 method file selection",
                max_count=max_candidates,
                require_non_empty=False,
            ),
            stage="stage4_method_outline",
            max_tokens=self.code_max_tokens,
            schema_name="stage4 file-wise method outline candidates",
            retries=1,
        )

    def run_stage4_method_global_rerank(
        self,
        *,
        issue: Dict[str, Any],
        reproducer: Dict[str, Any],
        execution: Dict[str, Any],
        log: Dict[str, Any],
        pass_trace: Dict[str, Any],
        stage1_result: CandidateResult,
        stage2_file_selection: CandidateResult,
        file_wise_method_result: Dict[str, Any],
        methods: List[MethodCandidate],
        final_top_k: int,
    ) -> RankedMethodResult:
        allowed = clean_ids([method.id for method in methods])
        if not allowed:
            return RankedMethodResult()
        if final_top_k > 0 and len(allowed) <= final_top_k:
            return RankedMethodResult(
                ranked_methods=[
                    RankedMethod(id=method_id, rank=index, reason="All selected methods fit within the final method budget.")
                    for index, method_id in enumerate(allowed, start=1)
                ]
            )
        return chat_json_with_validation(
            self.client,
            build_stage4_method_global_messages(
                issue=issue,
                reproducer=reproducer,
                execution=execution,
                log=log,
                pass_trace=pass_trace,
                stage1_result=stage1_result.to_dict(),
                stage2_file_selection=stage2_file_selection.to_dict(),
                file_wise_method_result=file_wise_method_result,
                method_candidates=[method.to_prompt_dict(include_body=True) for method in methods],
                final_top_k=final_top_k,
            ),
            lambda raw: require_ranked_methods(
                parse_ranked_method_result(raw, candidate_method_ids=allowed, max_methods=final_top_k),
                max_count=final_top_k,
                allowed_ids=allowed,
                pad_to_count=min(final_top_k, len(allowed)) if final_top_k > 0 else len(allowed),
                require_non_empty=True,
            ),
            stage="stage4_method_global",
            max_tokens=self.code_max_tokens,
            schema_name="stage4 global method ranking",
            retries=1,
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
    stage: str,
    max_count: int | None = None,
    expected_count: int | None = None,
    require_non_empty: bool = True,
) -> CandidateResult:
    if require_non_empty and not result.candidates:
        raise ValueError(f"{stage} returned no valid candidates from the allowed set")
    if max_count is not None and max_count > 0 and len(result.candidates) > max_count:
        result = CandidateResult(candidates=result.candidates[:max_count])
    if expected_count is not None and len(result.candidates) < expected_count:
        raise ValueError(f"{stage} returned {len(result.candidates)} candidates; expected {expected_count}")
    return result


def require_ranked_methods(
    result: RankedMethodResult,
    *,
    max_count: int,
    allowed_ids: List[str] | None = None,
    pad_to_count: int | None = None,
    require_non_empty: bool = True,
) -> RankedMethodResult:
    ranked_methods = sorted(result.ranked_methods, key=lambda item: item.rank)
    if require_non_empty and not ranked_methods:
        raise ValueError("stage4 global method rerank returned no valid methods from the allowed set")
    if max_count > 0:
        ranked_methods = ranked_methods[:max_count]
    if pad_to_count is not None and pad_to_count > len(ranked_methods):
        seen = {item.id for item in ranked_methods}
        for method_id in allowed_ids or []:
            if method_id in seen:
                continue
            ranked_methods.append(
                RankedMethod(
                    id=method_id,
                    rank=len(ranked_methods) + 1,
                    reason="Padding fallback: added from remaining allowed method candidates because the LLM returned fewer than the required top-k.",
                )
            )
            seen.add(method_id)
            if len(ranked_methods) >= pad_to_count:
                break
    return RankedMethodResult(
        ranked_methods=[
            RankedMethod(id=item.id, rank=index, reason=item.reason)
            for index, item in enumerate(ranked_methods, start=1)
        ]
    )
