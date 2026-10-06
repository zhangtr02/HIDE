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

Rules:
- id must exactly match one item from available_candidates.
- Do not invent ids outside available_candidates.
- Order candidates from most likely to least likely; candidates[0] is the top-1 prediction.
- Return exactly selection_limit items when the current task requires exact selection; otherwise return at most selection_limit items.
- Reasons should cite issue facts, reproducer facts, docs, code snippets, or prior stage reasoning.
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

Rules:
- id must exactly match one method_candidates[].id item.
- rank must be a positive integer; rank 1 is most likely.
- Return exactly final_top_k methods unless fewer candidates are available.
- Reasons should compare method bodies, method names, signatures, line ranges, file context, and bug evidence.
- Do not add fields outside the schema.
"""


SYSTEM_PROMPT = """You are the LocalizationAgent for GCC compiler bug isolation.

Inputs are limited to structured GCC issue facts, structured reproducer facts, structured execution/log facts, pre-collected GCC pass trace facts, GCC repository structure, offline module/file docs, and candidate method bodies shown in the prompt.

Candidate scope:
- Stage1 selects GCC modules from available_candidates.
- Stage2 selects GCC files from available_candidates inside Stage1-selected modules.
- Stage4 first selects functions/methods from Stage2-selected files using method outlines, then globally reranks the selected methods using full method bodies.

Do not use or assume rustc query traces. GCC has no query system in this pipeline; the pass_trace field is pre-collected GCC -fdump-passes evidence.
"""


STAGE_REASONING_GUIDE = """Stage responsibilities:
- Use structured issue and reproducer facts as the bug description.
- Use execution and log facts as observed GCC run behavior, diagnostics, ICE locations, and source spans.
- Use pass_trace as execution evidence about which GCC pass pipeline stages were enabled or reached.
- Use docs, method signatures, line ranges, and method bodies according to the current stage inputs.
- Stage4 file-wise method screening receives method outlines without bodies.
- Stage4 global method reranking receives full method bodies.
- Select or rank only candidates in the provided candidate scope.
- Treat module/file selection limits as upper bounds, not quotas.
- For method ranking, return exactly final_top_k methods unless fewer candidates are available.

Semantic role distinction:
- Surface trigger: code appearing in diagnostics, warnings, ICE stack traces, assertion sites, or immediate failure paths.
- Intermediate compiler mechanism: code that checks, transforms, optimizes, analyzes, emits diagnostics, or enforces shared GCC representations.
- Upstream source: code that parses, resolves, lowers, expands, or propagates compiler state later consumed by intermediate or surface-trigger code.
- When comparing candidates, describe the semantic role each candidate plays for the observed behavior.
"""


def build_stage1_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    execution: Dict[str, Any],
    log: Dict[str, Any],
    pass_trace: Dict[str, Any],
    module_docs: List[Dict[str, Any]],
    available_modules: List[str],
    max_candidates: int,
) -> List[Dict[str, Any]]:
    selection_limit = min(max_candidates, len(available_modules)) if max_candidates > 0 else len(available_modules)
    payload = {
        "task": "Stage1 module localization. Select GCC modules for the next localization stage.",
        "level": "module",
        "available_candidates": available_modules,
        "max_candidates": max_candidates,
        "selection_limit": selection_limit,
        "issue": issue,
        "reproducer": reproducer,
        "execution": compact_execution(execution),
        "log": compact_log(log),
        "pass_trace": compact_pass_trace(pass_trace),
        "module_docs": compact_docs(module_docs),
        "reasoning_guide": STAGE_REASONING_GUIDE,
        "output_contract": CANDIDATE_SCHEMA,
    }
    return messages(payload)


def build_stage2_file_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    execution: Dict[str, Any],
    log: Dict[str, Any],
    pass_trace: Dict[str, Any],
    stage1_result: Dict[str, Any],
    candidate_modules: List[str],
    file_docs: List[Dict[str, Any]],
    available_files: List[str],
    max_candidates: int,
    screening_context: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    selection_limit = min(max_candidates, len(available_files)) if max_candidates > 0 else len(available_files)
    compacted_file_docs = compact_docs(file_docs)
    context = screening_context or {}
    context_doc_mode = str(context.get("file_doc_mode") or "").strip()
    file_doc_mode = context_doc_mode or ("available" if compacted_file_docs else "unavailable")
    payload = {
        "task": "Stage2 file localization. Select and rank GCC source files whose responsibility or path context best matches the bug.",
        "level": "file_screening",
        "screening_context": context,
        "candidate_modules_from_stage1": candidate_modules,
        "stage1_result": stage1_result,
        "available_candidates": available_files,
        "max_candidates": max_candidates,
        "selection_limit": selection_limit,
        "issue": issue,
        "reproducer": reproducer,
        "execution": compact_execution(execution),
        "log": compact_log(log),
        "pass_trace": compact_pass_trace(pass_trace),
        "file_doc_mode": file_doc_mode,
        "file_docs": compacted_file_docs,
        "reasoning_guide": (
            STAGE_REASONING_GUIDE
            + "\nStage2 receives one GCC module group at a time."
            + "\nUse available_candidates and file_docs for the current group."
            + "\nIf a candidate has an empty doc, use its path, filename, module context, issue facts, reproducer facts, and pass_trace."
            + "\nSource code is not included in this stage."
            + "\nReturn at most selection_limit files, ordered from most likely root-cause file to least likely."
            + "\nThe returned order is used directly for file Top1, Top3, Top5, and Top10 evaluation."
            + "\nFor the final global merge context, candidates[0] is the final file Top1 prediction."
        ),
        "output_contract": CANDIDATE_SCHEMA,
    }
    return messages(payload)


def build_stage4_method_file_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    execution: Dict[str, Any],
    log: Dict[str, Any],
    pass_trace: Dict[str, Any],
    stage1_result: Dict[str, Any],
    stage2_file_selection: Dict[str, Any],
    file_id: str,
    method_candidates: List[Dict[str, Any]],
    max_candidates: int,
) -> List[Dict[str, Any]]:
    available_methods = [str(item.get("id") or "") for item in method_candidates if isinstance(item, dict)]
    selection_limit = min(max_candidates, len(available_methods)) if max_candidates > 0 else len(available_methods)
    payload = {
        "task": "Stage4 file-wise method outline screening. Select suspicious GCC functions/methods in one candidate file for full-body global reranking.",
        "level": "method_outline_screening",
        "file": file_id,
        "available_candidates": available_methods,
        "max_candidates": max_candidates,
        "selection_limit": selection_limit,
        "issue": issue,
        "reproducer": reproducer,
        "execution": compact_execution(execution),
        "log": compact_log(log),
        "pass_trace": compact_pass_trace(pass_trace),
        "stage1_result": stage1_result,
        "stage2_file_selection": stage2_file_selection,
        "method_candidates": method_candidates,
        "reasoning_guide": (
            STAGE_REASONING_GUIDE
            + "\nStage4 file-wise selection receives methods from one candidate file."
            + "\nmethod_candidates include method ids, files, signatures, item names, and line ranges. Method bodies are not included in this screening step."
            + "\nSelect only methods from the current file."
            + "\nReturn up to selection_limit methods. It is acceptable to return fewer when only fewer methods are strongly related."
        ),
        "output_contract": CANDIDATE_SCHEMA,
    }
    return messages(payload)


def build_stage4_method_global_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    execution: Dict[str, Any],
    log: Dict[str, Any],
    pass_trace: Dict[str, Any],
    stage1_result: Dict[str, Any],
    stage2_file_selection: Dict[str, Any],
    file_wise_method_result: Dict[str, Any],
    method_candidates: List[Dict[str, Any]],
    final_top_k: int,
) -> List[Dict[str, Any]]:
    candidate_methods = [str(item.get("id") or "") for item in method_candidates if isinstance(item, dict)]
    payload = {
        "task": "Stage4 global method reranking. Rank the selected GCC function/method candidates.",
        "level": "method_global_reranking",
        "candidate_methods": candidate_methods,
        "final_top_k": min(final_top_k, len(candidate_methods)) if final_top_k > 0 else len(candidate_methods),
        "issue": issue,
        "reproducer": reproducer,
        "execution": compact_execution(execution),
        "log": compact_log(log),
        "pass_trace": compact_pass_trace(pass_trace),
        "stage1_result": stage1_result,
        "stage2_file_selection": stage2_file_selection,
        "file_wise_method_result": file_wise_method_result,
        "method_candidates": method_candidates,
        "reasoning_guide": (
            STAGE_REASONING_GUIDE
            + "\nStage4 global reranking receives method candidates selected from individual files."
            + "\nmethod_candidates include method ids, files, signatures, item names, line ranges, and full method bodies."
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
        item = {
            "id": doc_id,
            "responsibility": truncate(str(doc.get("responsibility") or ""), max_responsibility_chars),
        }
        distinction = str(doc.get("same_name_distinction") or "").strip()
        if distinction:
            item["same_name_distinction"] = truncate(distinction, 600)
        compacted.append(item)
    return compacted


def compact_execution(execution: Dict[str, Any], *, max_command_samples: int = 4) -> Dict[str, Any]:
    if not isinstance(execution, dict):
        return {"available": False}
    samples = execution.get("command_samples") if isinstance(execution.get("command_samples"), list) else []
    compact_samples: List[Dict[str, Any]] = []
    for sample in samples[:max_command_samples]:
        if not isinstance(sample, dict):
            continue
        compact_samples.append(
            {
                key: sample.get(key)
                for key in ["path", "language", "return_code", "timed_out", "elapsed_sec"]
                if sample.get(key) not in (None, "", [])
            }
        )
    return {
        "available": bool(execution.get("available")),
        "source": execution.get("source", "fdump-passes"),
        "case_count": execution.get("case_count", 0),
        "languages": execution.get("languages") if isinstance(execution.get("languages"), list) else [],
        "options": execution.get("options") if isinstance(execution.get("options"), list) else [],
        "failed_count": execution.get("failed_count", 0),
        "timed_out_count": execution.get("timed_out_count", 0),
        "return_codes": execution.get("return_codes") if isinstance(execution.get("return_codes"), dict) else {},
        "unavailable_reason": execution.get("unavailable_reason", ""),
        "command_samples": compact_samples,
    }


def compact_log(log: Dict[str, Any], *, max_items: int = 16) -> Dict[str, Any]:
    if not isinstance(log, dict):
        return {"available": False}
    diagnostics = log.get("diagnostics") if isinstance(log.get("diagnostics"), list) else []
    compact_diagnostics: List[Dict[str, Any]] = []
    for item in diagnostics[:max_items]:
        if not isinstance(item, dict):
            continue
        compact_diagnostics.append(
            {
                "path": item.get("path", ""),
                "language": item.get("language", ""),
                "return_code": item.get("return_code"),
                "timed_out": bool(item.get("timed_out")),
                "class": item.get("class", ""),
                "messages": item.get("messages") if isinstance(item.get("messages"), list) else [],
                "ice_locations": item.get("ice_locations") if isinstance(item.get("ice_locations"), list) else [],
                "compiler_paths_mentioned": item.get("compiler_paths_mentioned") if isinstance(item.get("compiler_paths_mentioned"), list) else [],
                "source_spans": item.get("source_spans") if isinstance(item.get("source_spans"), list) else [],
            }
        )
    return {
        "available": bool(log.get("available")),
        "availability": log.get("availability", ""),
        "case_count": log.get("case_count", 0),
        "failure_kinds": log.get("failure_kinds") if isinstance(log.get("failure_kinds"), list) else [],
        "diagnostic_messages": log.get("diagnostic_messages") if isinstance(log.get("diagnostic_messages"), list) else [],
        "ice_locations": log.get("ice_locations") if isinstance(log.get("ice_locations"), list) else [],
        "compiler_paths_mentioned": log.get("compiler_paths_mentioned") if isinstance(log.get("compiler_paths_mentioned"), list) else [],
        "source_spans": log.get("source_spans") if isinstance(log.get("source_spans"), list) else [],
        "diagnostics": compact_diagnostics,
    }


def compact_pass_trace(pass_trace: Dict[str, Any], *, max_passes: int = 160) -> Dict[str, Any]:
    if not isinstance(pass_trace, dict) or not pass_trace.get("available"):
        return {
            "available": False,
            "availability": pass_trace.get("availability", "unavailable") if isinstance(pass_trace, dict) else "unavailable",
            "unavailable_reason": pass_trace.get("unavailable_reason", "") if isinstance(pass_trace, dict) else "",
            "case_summary": pass_trace.get("case_summary", {}) if isinstance(pass_trace, dict) else {},
        }
    passes = pass_trace.get("passes") if isinstance(pass_trace.get("passes"), list) else []
    high_signal_passes = pass_trace.get("high_signal_passes") if isinstance(pass_trace.get("high_signal_passes"), list) else []
    compact_passes: List[Dict[str, Any]] = []
    pass_source = high_signal_passes if high_signal_passes else passes
    for item in pass_source[:max_passes]:
        if not isinstance(item, dict):
            continue
        compact_passes.append(
            {
                key: item.get(key)
                for key in ["name", "kind", "stage"]
                if item.get(key) not in (None, "", [])
            }
        )
    return {
        "available": True,
        "availability": pass_trace.get("availability", "available"),
        "source": pass_trace.get("source", "fdump-passes"),
        "pass_count": pass_trace.get("pass_count", len(passes)),
        "stage_counts": pass_trace.get("stage_counts") if isinstance(pass_trace.get("stage_counts"), dict) else {},
        "special_pass_families": pass_trace.get("special_pass_families") if isinstance(pass_trace.get("special_pass_families"), dict) else {},
        "languages": pass_trace.get("languages") if isinstance(pass_trace.get("languages"), list) else [],
        "options": pass_trace.get("options") if isinstance(pass_trace.get("options"), list) else [],
        "passes": compact_passes,
        "failed_cases": pass_trace.get("failed_cases") if isinstance(pass_trace.get("failed_cases"), list) else [],
    }


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
