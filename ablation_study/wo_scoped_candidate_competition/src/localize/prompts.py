from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List


CANDIDATE_SCHEMA = """Return JSON only.

Schema:
{
  "candidates": [
    {
      "id": "candidate id",
      "reason": "evidence-based rationale"
    }
  ]
}

Example:
{
  "candidates": [
    {
      "id": "one_exact_id_from_available_candidates",
      "reason": "Brief evidence-based rationale."
    }
  ]
}

Rules:
- id must exactly match one item from available_candidates.
- Do not invent ids outside available_candidates.
- Order candidates from most likely to least likely; candidates[0] is the top-1 prediction.
- Return exactly selection_count items when selection_count is provided; otherwise return at most max_candidates items.
- Reasons should cite evidence, docs, issue/reproducer facts, or prior stage reasoning.
- Do not add fields outside the schema.
"""


RANKED_METHOD_SCHEMA = """Return JSON only.

Schema:
{
  "ranked_methods": [
    {
      "id": "method candidate id",
      "rank": 1,
      "reason": "evidence-based rationale"
    }
  ]
}

Example:
{
  "ranked_methods": [
    {
      "id": "one_exact_id_from_candidate_methods",
      "rank": 1,
      "reason": "Brief evidence-based rationale."
    }
  ]
}

Rules:
- id must exactly match one item from candidate_methods.
- rank must be a positive integer; rank 1 is most likely.
- Return exactly final_top_k methods unless fewer candidates are available.
- Reasons should compare method bodies, signatures, file context, and evidence.
- Do not add fields outside the schema.
"""


SYSTEM_PROMPT = """You are the LocalizationAgent for rustc compiler bug isolation.

The static evidence extractor provides structured facts from logs and query traces. It does not rank, score, map, or localize candidates.

Candidate scope:
- Stage1 selects crates from available_candidates.
- Stage2 selects modules from available_candidates under Stage1-selected crates.
- Stage3 selects files from available_candidates under Stage2-selected modules or crate root-file groups.
- Stage4 selects methods from available candidate methods in Stage3-selected files.

Evidence fields:
- query_trace.queries are rustc queries observed during the bug run, not precomputed candidate files.
- query_key_samples and query_key_terms describe the reproducer's program items/types involved in those queries.
- compiler_paths_mentioned are paths printed by rustc logs.
- docs describe candidate responsibilities.
- source snippets and method bodies are partial code views provided for the current candidate scope.
"""


STAGE_REASONING_GUIDE = """Stage responsibilities:
- Use issue and reproducer facts as the bug description.
- Use structured evidence fields as observations from the execution.
- Use docs, outlines, snippets, and method bodies according to the current stage inputs.
- Select or rank only candidates in the provided candidate scope.
- Return the required number of candidates when a required count is provided.

Semantic role distinction:
- Surface trigger: code appearing in logs, query stack, diagnostics, assertion sites, panic sites, invariant checks, or immediate failure paths.
- Intermediate representation or invariant layer: code that defines, wraps, converts, checks, or enforces shared compiler representations and invariants.
- Upstream source: code that constructs, lowers, resolves, transforms, or propagates compiler state later consumed by intermediate-layer or surface-trigger code.
- When comparing candidates, describe the semantic role each candidate plays for the observed behavior.
"""


def build_stage1_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    evidence: Dict[str, Any],
    crate_docs: List[Dict[str, Any]],
    available_crates: List[str],
    max_candidates: int,
) -> List[Dict[str, Any]]:
    selection_count = min(max_candidates, len(available_crates)) if max_candidates > 0 else len(available_crates)
    payload = {
        "task": "Stage1 crate localization. Select rustc compiler crates for the next localization stage.",
        "level": "crate",
        "available_candidates": available_crates,
        "max_candidates": max_candidates,
        "selection_count": selection_count,
        "issue": issue,
        "reproducer": reproducer,
        "evidence": compact_evidence_for_llm(evidence),
        "crate_docs": compact_docs(crate_docs),
        "reasoning_guide": STAGE_REASONING_GUIDE,
        "output_contract": CANDIDATE_SCHEMA,
    }
    return messages(payload)


def build_stage2_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    evidence: Dict[str, Any],
    stage1_result: Dict[str, Any],
    candidate_crates: List[str],
    module_docs: List[Dict[str, Any]],
    available_modules: List[str],
    max_candidates: int,
    screening_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    selection_count = min(max_candidates, len(available_modules)) if max_candidates > 0 else len(available_modules)
    payload = {
        "task": "Stage2 module localization. Select modules inside Stage1-selected crates for the next localization stage.",
        "level": "module",
        "screening_context": screening_context or {},
        "candidate_crates_from_stage1": candidate_crates,
        "stage1_result": stage1_result,
        "available_candidates": available_modules,
        "max_candidates": max_candidates,
        "selection_count": selection_count,
        "issue": issue,
        "reproducer": reproducer,
        "evidence": compact_evidence_for_llm(evidence),
        "module_docs": compact_docs(module_docs),
        "reasoning_guide": STAGE_REASONING_GUIDE,
        "output_contract": CANDIDATE_SCHEMA,
    }
    return messages(payload)


def build_stage3_doc_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    evidence: Dict[str, Any],
    stage1_result: Dict[str, Any],
    stage2_result: Dict[str, Any],
    candidate_modules: List[str],
    candidate_root_files: List[str],
    file_docs: List[Dict[str, Any]],
    available_files: List[str],
    max_candidates: int,
    screening_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    selection_count = min(max_candidates, len(available_files)) if max_candidates > 0 else len(available_files)
    payload = {
        "task": "Stage3 file localization. Select and rank files for method-level localization using file responsibility docs.",
        "level": "file_doc_screening",
        "ablation": "w/o scoped candidate competition",
        "scoped_candidate_competition": False,
        "screening_context": screening_context or {},
        "candidate_modules_from_stage2": candidate_modules,
        "candidate_root_files": candidate_root_files,
        "stage1_result": stage1_result,
        "stage2_result": stage2_result,
        "available_candidates": available_files,
        "max_candidates": max_candidates,
        "selection_count": selection_count,
        "issue": issue,
        "reproducer": reproducer,
        "evidence": compact_evidence_for_llm(evidence),
        "file_docs": compact_docs(file_docs),
        "reasoning_guide": (
            STAGE_REASONING_GUIDE
            + "\nStage3 receives one global file candidate pool from all selected modules and selected crate root files."
            + "\nUse available_candidates and file_docs for the full global file pool."
            + "\nSource code is not included in this stage."
            + "\nReturn exactly selection_count files unless fewer candidates are available."
            + "\nOrder returned files from most likely root-cause file to least likely."
            + "\nThe returned order is used directly for file Top1, Top3, Top5, and Top10 evaluation."
            + "\nThere is no module-wise preselection or later file merge in this ablation."
            + "\nCandidates[0] is the final file Top1 prediction."
        ),
        "output_contract": CANDIDATE_SCHEMA,
    }
    return messages(payload)


def build_stage4_method_global_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    evidence: Dict[str, Any],
    stage1_result: Dict[str, Any],
    stage2_result: Dict[str, Any],
    stage3_file_selection: Dict[str, Any],
    method_pool_context: Dict[str, Any],
    method_candidates: List[Dict[str, Any]],
    final_top_k: int,
) -> List[Dict[str, Any]]:
    candidate_methods = [str(item.get("id") or "") for item in method_candidates if isinstance(item, dict)]
    payload = {
        "task": "Stage4 global method reranking. Rank the selected method candidates.",
        "level": "method_global_reranking",
        "ablation": "w/o scoped candidate competition",
        "scoped_candidate_competition": False,
        "candidate_methods": candidate_methods,
        "final_top_k": min(final_top_k, len(candidate_methods)) if final_top_k > 0 else len(candidate_methods),
        "issue": issue,
        "reproducer": reproducer,
        "evidence": compact_evidence_for_llm(evidence),
        "stage1_result": stage1_result,
        "stage2_result": stage2_result,
        "stage3_file_selection": stage3_file_selection,
        "method_pool_context": method_pool_context,
        "method_candidates": method_candidates,
        "reasoning_guide": (
            STAGE_REASONING_GUIDE
            + "\nStage4 receives one global method candidate pool from the Stage3 file candidates."
            + "\nmethod_candidates include method ids, signatures, parent impl/trait context, line ranges, and method bodies."
            + "\nThere is no file-wise method preselection in this ablation."
            + "\nReturn exactly final_top_k methods unless fewer candidates are available."
        ),
        "output_contract": RANKED_METHOD_SCHEMA,
    }
    return messages(payload)


def messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def compact_docs(docs: Iterable[Dict[str, Any]], *, max_responsibility_chars: int = 1200) -> List[Dict[str, str]]:
    compacted: List[Dict[str, str]] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        doc_id = str(doc.get("id") or "").strip()
        if not doc_id:
            continue
        item = {"id": doc_id, "responsibility": truncate(str(doc.get("responsibility") or ""), max_responsibility_chars)}
        distinction = str(doc.get("same_name_distinction") or "").strip()
        if distinction:
            item["same_name_distinction"] = truncate(distinction, 600)
        compacted.append(item)
    return compacted


def compact_evidence_for_llm(evidence: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(evidence, dict):
        return {}
    query_trace = evidence.get("query_trace") if isinstance(evidence.get("query_trace"), dict) else {}
    log = evidence.get("log") if isinstance(evidence.get("log"), dict) else {}
    execution = evidence.get("execution") if isinstance(evidence.get("execution"), dict) else {}
    return {
        "execution": {
            "toolchain": execution.get("toolchain", ""),
            "return_code": execution.get("return_code"),
            "kind": execution.get("kind", ""),
            "rustflags": execution.get("rustflags", []),
        },
        "log": {
            "error_codes": compact_items(log.get("error_codes", []), 20),
            "diagnostic_messages": compact_items(log.get("diagnostic_messages", []), 20),
            "ice_message": log.get("ice_message", ""),
            "panic_messages": compact_items(log.get("panic_messages", []), 8),
            "panic_modes": compact_items(log.get("panic_modes", []), 20),
            "query_stack": compact_items(log.get("query_stack", []), 20),
            "compiler_paths_mentioned": compact_items(log.get("compiler_paths_mentioned", []), 20),
            "source_spans": compact_items(log.get("source_spans", []), 20),
            "rustc_symbols": compact_items(log.get("rustc_symbols", []), 20),
        },
        "query_trace": {
            "available": bool(query_trace.get("available")) or str(query_trace.get("availability", "")).lower() == "available",
            "status": query_trace.get("status", query_trace.get("availability", "")),
            "queries": compact_items(query_trace.get("queries", []), 80),
            "query_key_samples": compact_items(query_trace.get("query_key_samples", []), 30),
            "query_key_terms": compact_items(query_trace.get("query_key_terms", []), 80),
        },
    }


def compact_items(value: Any, limit: int) -> Any:
    if isinstance(value, list):
        return value[:limit]
    if isinstance(value, tuple):
        return list(value[:limit])
    if isinstance(value, dict):
        return {str(key): compact_items(item, limit) for key, item in value.items()}
    if value in (None, "", []):
        return []
    return [str(value)][:limit]


def truncate(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars] + "... [truncated]"
