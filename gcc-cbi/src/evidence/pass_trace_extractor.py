from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.json_io import load_json_if_exists, unique_keep_order


SPECIAL_PASS_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "analyzer": ("analyzer",),
    "asan": ("asan",),
    "ubsan": ("ubsan", "sanopt", "sancov"),
    "coroutine": ("coro",),
    "vectorization": ("vect", "slp",),
    "graphite_loop": ("graphite", "parloops",),
    "openacc_openmp": ("oacc", "openacc", "ompdev", "ompexp", "omplower",),
    "uninitialized_warning": ("uninit", "early_uninit",),
    "access_warning": ("waccess", "walloca", "strlen", "printf",),
    "value_range": ("vrp", "evrp",),
    "threading": ("thread",),
    "rtl_register": ("ira", "lra", "reload", "rnreg", "web",),
    "rtl_scheduling": ("sched",),
}

COMMON_PASS_NAMES = {
    "tree-omplower",
    "tree-lower",
    "tree-eh",
    "tree-cfg",
    "tree-ompexp",
    "tree-ssa",
    "tree-einline",
    "tree-release_ssa",
    "ipa-visibility",
    "ipa-inline",
    "ipa-modref",
    "rtl-expand",
    "rtl-final",
}


def extract_pass_trace_evidence(pass_trace_dir: Path, *, bug_id: str, max_passes: int = 200) -> Dict[str, Any]:
    path = pass_trace_dir / f"{bug_id}.json"
    obj = load_json_if_exists(path)
    if not obj:
        return empty_pass_trace(bug_id, availability="missing")

    passes = obj.get("passes") if isinstance(obj.get("passes"), list) else []
    available = bool(obj.get("available")) and bool(passes)
    case_items = obj.get("cases") if isinstance(obj.get("cases"), list) else []
    options = compact_options(obj.get("options") if isinstance(obj.get("options"), list) else [])
    special_families = extract_special_pass_families(passes)
    high_signal = extract_high_signal_passes(passes, special_families=special_families, limit=max_passes)

    return {
        "instance_id": bug_id,
        "source": obj.get("source", "fdump-passes"),
        "availability": "available" if available else "unavailable",
        "available": available,
        "compiler": obj.get("compiler") if isinstance(obj.get("compiler"), dict) else {},
        "case_count": int(obj.get("case_count") or len(case_items) or 0),
        "languages": obj.get("languages") if isinstance(obj.get("languages"), list) else [],
        "options": options,
        "pass_count": len(passes),
        "stage_counts": obj.get("stage_counts") if isinstance(obj.get("stage_counts"), dict) else {},
        "special_pass_families": special_families,
        "high_signal_passes": high_signal,
        "passes": compact_passes(passes[: max(0, max_passes)]),
        "failed_cases": obj.get("failed_cases") if isinstance(obj.get("failed_cases"), list) else [],
        "case_summary": summarize_cases(case_items),
        "unavailable_reason": "" if available else classify_unavailable_reason(case_items),
    }


def empty_pass_trace(bug_id: str, *, availability: str) -> Dict[str, Any]:
    return {
        "instance_id": bug_id,
        "source": "fdump-passes",
        "availability": availability,
        "available": False,
        "compiler": {},
        "case_count": 0,
        "languages": [],
        "options": [],
        "pass_count": 0,
        "stage_counts": {},
        "special_pass_families": {},
        "high_signal_passes": [],
        "passes": [],
        "failed_cases": [],
        "case_summary": {},
        "unavailable_reason": availability,
    }


def compact_passes(passes: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compacted: List[Dict[str, Any]] = []
    for item in passes:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                key: item.get(key)
                for key in ["name", "kind", "stage", "properties_required", "properties_provided", "todo_flags_start", "todo_flags_finish"]
                if item.get(key) not in (None, "", [])
            }
        )
    return compacted


def compact_options(options: Iterable[Any]) -> List[str]:
    ignored = {"-o", "-c", "-S", "-E", "-fdump-passes"}
    ignored_prefixes = ("--prefix", "--enable-", "--disable-", "--with-", "--without-", "--build", "--host")
    out: List[str] = []
    seen: set[str] = set()
    for option in options:
        item = str(option).strip().strip("`'\".,;:")
        if not item or item in ignored or item.startswith(ignored_prefixes) or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def extract_special_pass_families(passes: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    families: Dict[str, List[str]] = {}
    pass_names = unique_keep_order(str(item.get("name") or "") for item in passes if isinstance(item, dict))
    for family, keywords in SPECIAL_PASS_KEYWORDS.items():
        matched = [
            name
            for name in pass_names
            if any(keyword in name.lower() for keyword in keywords)
            and name not in COMMON_PASS_NAMES
        ]
        if matched:
            families[family] = matched
    return families


def extract_high_signal_passes(
    passes: Iterable[Dict[str, Any]],
    *,
    special_families: Dict[str, List[str]],
    limit: int,
) -> List[Dict[str, Any]]:
    special_names = set(name for names in special_families.values() for name in names)
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in passes:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name or name in seen:
            continue
        if name in special_names or is_high_signal_name(name):
            seen.add(name)
            out.append(
                {
                    key: item.get(key)
                    for key in ["name", "kind", "stage"]
                    if item.get(key) not in (None, "", [])
                }
            )
        if len(out) >= limit:
            break
    return out


def is_high_signal_name(name: str) -> bool:
    lowered = name.lower()
    if name in COMMON_PASS_NAMES:
        return False
    return any(keyword in lowered for keywords in SPECIAL_PASS_KEYWORDS.values() for keyword in keywords)


def summarize_cases(cases: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    return_codes: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    timed_out = 0
    option_warning_count = 0
    case_count = 0
    failed_count = 0
    diagnostic_samples: List[Dict[str, Any]] = []

    for case in cases:
        if not isinstance(case, dict):
            continue
        case_count += 1
        languages[str(case.get("language") or "unknown")] += 1
        return_code = case.get("return_code")
        return_codes[str(return_code)] += 1
        if case.get("timed_out"):
            timed_out += 1
        if case.get("status"):
            status_counts[str(case.get("status"))] += 1
        if return_code not in (0, "0"):
            failed_count += 1
        warnings = case.get("option_warnings") if isinstance(case.get("option_warnings"), list) else []
        option_warning_count += len(warnings)
        sample = diagnostic_sample(case)
        if sample and len(diagnostic_samples) < 8:
            diagnostic_samples.append(sample)

    return {
        "case_count": case_count,
        "failed_count": failed_count,
        "timed_out_count": timed_out,
        "return_codes": dict(sorted(return_codes.items())),
        "languages": dict(sorted(languages.items())),
        "statuses": dict(sorted(status_counts.items())),
        "option_warning_count": option_warning_count,
        "diagnostic_samples": diagnostic_samples,
    }


def diagnostic_sample(case: Dict[str, Any]) -> Dict[str, Any]:
    text = str(case.get("output_excerpt") or "").strip()
    if not text and not case.get("timed_out"):
        return {}
    output_class = classify_output(text, timed_out=bool(case.get("timed_out")))
    if output_class in {"other", "empty_output"}:
        return {}
    sample = {
        "path": case.get("path", ""),
        "language": case.get("language", ""),
        "return_code": case.get("return_code"),
        "timed_out": bool(case.get("timed_out")),
        "class": output_class,
    }
    if output_class not in {"other", "empty_output"}:
        sample["message_excerpt"] = text[:600]
    return sample


def classify_unavailable_reason(cases: Iterable[Dict[str, Any]]) -> str:
    texts: List[str] = []
    timeout = False
    for case in cases:
        if not isinstance(case, dict):
            continue
        timeout = timeout or bool(case.get("timed_out"))
        texts.append(str(case.get("output_excerpt") or ""))
    return classify_output("\n".join(texts), timed_out=timeout)


def classify_output(text: str, *, timed_out: bool) -> str:
    if timed_out:
        return "timeout"
    if "unrecognized command-line option" in text:
        return "unrecognized_option"
    if "Coarrays disabled" in text:
        return "fortran_coarray_flag_missing"
    if "No such file or directory" in text or "cannot find" in text:
        return "missing_testsuite_support_file"
    if "internal compiler error" in text:
        return "ice_before_or_during_passes"
    if "output filename specified twice" in text:
        return "conflicting_output_options"
    if "linker input file unused because linking not done" in text:
        return "unsupported_file_suffix_or_language"
    if "error:" in text or "Error:" in text or "Fatal Error:" in text:
        return "compile_error_before_passes"
    if not text.strip():
        return "empty_output"
    return "other"
