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
- Return exactly selection_count items.
- Order candidates from most likely to least likely; candidates[0] is the top-1 prediction.
- Reasons should cite evidence, file docs, issue facts, or failure-program facts.
- Do not return shell commands, search commands, tool calls, or plans for further investigation.
- Do not add fields outside the schema.
"""


SYSTEM_PROMPT = """You are the LocalizationAgent for rustc compiler bug isolation.

This ablation disables crate localization, module localization, scoped candidate competition, and method localization.
Select suspicious files directly from every Rust source file under the current rustc compiler directory.
The structured evidence contains observations extracted from compiler logs and rustc query traces; it does not rank candidates.
No tools are available and no additional repository search can be performed.
Complete the localization using only the supplied inputs and return the required candidate JSON.
"""


REASONING_GUIDE = """Use the issue and failure program as the bug description.
Use structured evidence as observations from the failing execution.
Use file responsibility docs to compare every candidate file directly.
Distinguish surface triggers, intermediate representation or invariant sites, and upstream root-cause sites.
Select only exact ids from available_candidates.
Do not propose or emit repository-search commands; make the final selection from the supplied file ids and docs.
Return exactly selection_count files ordered from most likely root cause to least likely.
The returned order is used directly for File Top1, Top3, Top5, and Top10 evaluation.
"""


def build_file_localization_messages(
    *,
    issue: Dict[str, Any],
    reproducer: Dict[str, Any],
    evidence: Dict[str, Any],
    file_docs: List[Dict[str, Any]],
    available_files: List[str],
    max_candidates: int,
) -> List[Dict[str, Any]]:
    selection_count = min(max_candidates, len(available_files)) if max_candidates > 0 else len(available_files)
    payload = {
        "task": "Global file localization. Select and rank suspicious files directly from all rustc compiler files.",
        "level": "global_file_localization",
        "available_candidates": available_files,
        "selection_count": selection_count,
        "issue": issue,
        "reproducer": reproducer,
        "evidence": compact_evidence_for_llm(evidence),
        "file_docs": compact_docs(file_docs),
        "reasoning_guide": REASONING_GUIDE,
        "output_contract": CANDIDATE_SCHEMA,
    }
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
            "available": bool(query_trace.get("available"))
            or str(query_trace.get("availability", "")).lower() == "available",
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
