from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from src.common.json_io import load_json_if_exists
from src.evidence.pass_trace_extractor import classify_output, extract_pass_trace_evidence


MAX_ITEMS = 40

COMPILER_PATH_RE = re.compile(
    r"\b(?P<path>(?:gcc/)?(?:c|cp|fortran|objc|objcp|ada|go|d|jit|lto|analyzer|config|"
    r"c-family|common|tree|gimple|ipa|rtl|fold-const|match|value-range|diagnostic)"
    r"[\w./+-]*\.(?:cpp|cxx|cc|hpp|hh|c|h|def|opt|md|pd))(?=\b|:|$)"
    r"(?::(?P<line>\d+))?(?::(?P<column>\d+))?"
)
TEST_PATH_RE = re.compile(r"\bgcc/testsuite/[A-Za-z0-9_./+-]+")
SOURCE_SPAN_RE = re.compile(
    r"(?P<path>(?:/[\w.@+-]+)*/?(?:gcc/testsuite/)?[\w./+-]+\.(?:c|cc|cpp|cxx|C|h|H|i|ii|f|f90|f95|f03|f08|F|F90))"
    r":(?P<line>\d+):(?P<column>\d+)"
)
ICE_RE = re.compile(
    r"(?:internal compiler error|ICE)[^\n]*?(?:in\s+(?P<function>[A-Za-z_][\w:~<>.-]*),\s*)?"
    r"at\s+(?P<path>(?:gcc/)?[\w./+-]+\.(?:cpp|cxx|cc|hpp|hh|c|h)):(?P<line>\d+)",
    re.IGNORECASE,
)
DURING_PASS_RE = re.compile(r"\bduring\s+(?:GIMPLE|RTL|IPA|TREE)?\s*pass:?\s*([A-Za-z0-9_.+*-]+)", re.IGNORECASE)
OPTION_TOKEN_RE = re.compile(
    r"(?<![\w/])("
    r"--param=[^\s,;(){}\"']+|"
    r"--[A-Za-z][\w-]*(?:=[^\s,;(){}\"']+)?|"
    r"-std=[^\s,;(){}\"']+|"
    r"-O[0-3sgfastz]*|"
    r"-m[\w-]+(?:=[^\s,;(){}\"']+)?|"
    r"-f(?:no-)?[\w-]+(?:=[^\s,;(){}\"']+)?|"
    r"-W[\w-]+(?:=[^\s,;(){}\"']+)?|"
    r"-D[^\s,;(){}\"']+|"
    r"-I[^\s,;(){}\"']+"
    r")"
)
TARGET_TRIPLE_RE = re.compile(
    r"\b(?:x86_64|i[3-6]86|aarch64|arm|riscv(?:32|64)?|powerpc(?:64)?|s390x|mips(?:64)?|"
    r"loongarch64|sparc(?:64)?|nvptx|amdgcn)[\w.-]*-(?:pc-)?[\w.-]*\b"
)
DG_OPTIONS_RE = re.compile(r"\{\s*dg-(?:additional-)?options\s+\"([^\"]*)\"", re.IGNORECASE)
DG_DO_RE = re.compile(r"\{\s*dg-do\s+(.+?)\s*\}", re.IGNORECASE)
DG_FINAL_RE = re.compile(r"\{\s*dg-final\s+(.+?)\s*\}", re.IGNORECASE)
DG_EXPECT_RE = re.compile(r"\{\s*dg-(?:error|warning|message|bogus)\s+\"([^\"]*)\"", re.IGNORECASE)
TARGET_STANDARD_RE = re.compile(r"\btarget\s+((?:c\+\+|c)\d{2,})\b", re.IGNORECASE)
INCLUDE_RE = re.compile(r"^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", re.MULTILINE)
BUILTIN_RE = re.compile(r"\b__builtin_[A-Za-z0-9_]+\b")
ATTRIBUTE_RE = re.compile(r"__attribute__\s*\(\(([^)]{1,160})\)\)")
PRAGMA_RE = re.compile(r"^\s*#\s*pragma\s+([A-Za-z0-9_ ][^\n]*)", re.MULTILINE)
MACRO_RE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)", re.MULTILINE)
FUNCTION_DEF_RE = re.compile(
    r"(?m)^\s*(?:template\s*<[^;{}]{0,200}>\s*)?"
    r"(?:[A-Za-z_][\w:<>~*&\s,]*\s+)?(?P<name>[A-Za-z_~]\w*)\s*"
    r"\([^;{}]{0,300}\)\s*(?:const\s*)?(?:noexcept\s*)?(?:->\s*[^;{]+)?\s*\{"
)
TYPE_DECL_RE = re.compile(r"\b(?:struct|class|union|enum|concept)\s+([A-Za-z_]\w*)")


DOMAIN_PATTERNS: Dict[str, tuple[str, ...]] = {
    "c++_templates": ("template", "typename", "concept", "requires", "constrained auto"),
    "constexpr": ("constexpr", "consteval", "constant expression"),
    "coroutines": ("co_await", "co_yield", "co_return", "coroutine"),
    "modules": ("export module", "import ", "-fmodules-ts"),
    "openmp_openacc": ("#pragma omp", "#pragma acc", "-fopenmp", "-fopenacc"),
    "vectorization": ("-ftree-vectorize", "vectorized", "vectorizer", "simd", "-msse", "-mavx", "-mneon"),
    "sanitizer": ("sanitize", "asan", "ubsan", "-fsanitize"),
    "analyzer": ("-fanalyzer", "analyzer"),
    "value_range": ("vrp", "ranger", "range", "overflow"),
    "warnings_diagnostics": ("warning:", "error:", "diagnostic", "dg-warning", "dg-error"),
    "rtl_registers": ("reload", "lra", "ira", "register allocation"),
    "target_specific": ("target:", "march", "mtune", "abi", "backend"),
    "fortran": ("gfortran", "fortran", "coarray", "allocatable", "subroutine"),
}


class EvidenceExtractor:
    def __init__(self, *, pass_trace_dir: Path, issue_dir: Path | None = None, reproducer_dir: Path | None = None) -> None:
        self.pass_trace_dir = pass_trace_dir

    def extract(
        self,
        *,
        bug_id: str,
        issue_max_chars: int = 24000,
        reproducer_max_chars: int = 24000,
        pass_trace_max_items: int = 200,
    ) -> Dict[str, Any]:
        raw_pass_trace = load_json_if_exists(self.pass_trace_dir / f"{bug_id}.json") or {}
        pass_trace = extract_pass_trace_evidence(self.pass_trace_dir, bug_id=bug_id, max_passes=pass_trace_max_items)
        pass_trace["option_summary"] = summarize_options(pass_trace.get("options") if isinstance(pass_trace, dict) else [])

        return normalize_evidence_schema(
            {
            "bug_id": bug_id,
            "execution": extract_execution_evidence(raw_pass_trace, pass_trace, bug_id=bug_id),
            "log": extract_log_evidence(raw_pass_trace, bug_id=bug_id),
            "pass_trace": pass_trace,
            },
            bug_id=bug_id,
        )


def normalize_evidence_schema(evidence: Dict[str, Any], *, bug_id: str = "") -> Dict[str, Any]:
    pass_trace = evidence.get("pass_trace") if isinstance(evidence.get("pass_trace"), dict) else {}
    instance_id = str(evidence.get("bug_id") or pass_trace.get("instance_id") or bug_id)
    execution = evidence.get("execution") if isinstance(evidence.get("execution"), dict) else {}
    log = evidence.get("log") if isinstance(evidence.get("log"), dict) else {}
    if not execution:
        execution = fallback_execution_from_pass_trace(pass_trace, bug_id=instance_id)
    if not log:
        log = empty_log(instance_id, availability="not_collected")
    return {
        "bug_id": instance_id,
        "execution": execution,
        "log": log,
        "pass_trace": pass_trace,
    }


def extract_execution_evidence(raw_pass_trace: Dict[str, Any], pass_trace: Dict[str, Any], *, bug_id: str) -> Dict[str, Any]:
    cases = raw_pass_trace.get("cases") if isinstance(raw_pass_trace.get("cases"), list) else []
    case_summary = pass_trace.get("case_summary") if isinstance(pass_trace.get("case_summary"), dict) else {}
    command_samples: List[Dict[str, Any]] = []
    for case in cases[:8]:
        if not isinstance(case, dict):
            continue
        command = case.get("command") if isinstance(case.get("command"), list) else []
        command_samples.append(
            {
                "path": case.get("path", ""),
                "language": case.get("language", ""),
                "command": command,
                "return_code": case.get("return_code"),
                "timed_out": bool(case.get("timed_out")),
                "elapsed_sec": case.get("elapsed_sec"),
            }
        )
    return {
        "instance_id": bug_id,
        "source": raw_pass_trace.get("source", pass_trace.get("source", "fdump-passes")),
        "available": bool(raw_pass_trace.get("available")) or bool(pass_trace.get("available")),
        "pass_trace_available": bool(pass_trace.get("available")),
        "compiler": raw_pass_trace.get("compiler") if isinstance(raw_pass_trace.get("compiler"), dict) else pass_trace.get("compiler", {}),
        "case_count": int(raw_pass_trace.get("case_count") or pass_trace.get("case_count") or len(cases) or 0),
        "languages": raw_pass_trace.get("languages") if isinstance(raw_pass_trace.get("languages"), list) else pass_trace.get("languages", []),
        "options": filter_localization_options(raw_pass_trace.get("options") if isinstance(raw_pass_trace.get("options"), list) else pass_trace.get("options", [])),
        "failed_count": case_summary.get("failed_count", 0),
        "timed_out_count": case_summary.get("timed_out_count", 0),
        "return_codes": case_summary.get("return_codes", {}),
        "unavailable_reason": pass_trace.get("unavailable_reason", ""),
        "command_samples": command_samples,
    }


def fallback_execution_from_pass_trace(pass_trace: Dict[str, Any], *, bug_id: str) -> Dict[str, Any]:
    case_summary = pass_trace.get("case_summary") if isinstance(pass_trace.get("case_summary"), dict) else {}
    return {
        "instance_id": bug_id,
        "source": pass_trace.get("source", "fdump-passes"),
        "available": bool(pass_trace.get("available")),
        "pass_trace_available": bool(pass_trace.get("available")),
        "compiler": pass_trace.get("compiler") if isinstance(pass_trace.get("compiler"), dict) else {},
        "case_count": pass_trace.get("case_count", 0),
        "languages": pass_trace.get("languages") if isinstance(pass_trace.get("languages"), list) else [],
        "options": pass_trace.get("options") if isinstance(pass_trace.get("options"), list) else [],
        "failed_count": case_summary.get("failed_count", 0),
        "timed_out_count": case_summary.get("timed_out_count", 0),
        "return_codes": case_summary.get("return_codes", {}),
        "unavailable_reason": pass_trace.get("unavailable_reason", ""),
        "command_samples": [],
    }


def extract_log_evidence(raw_pass_trace: Dict[str, Any], *, bug_id: str) -> Dict[str, Any]:
    cases = raw_pass_trace.get("cases") if isinstance(raw_pass_trace.get("cases"), list) else []
    if not raw_pass_trace:
        return empty_log(bug_id, availability="missing_pass_trace")

    diagnostics: List[Dict[str, Any]] = []
    failure_kinds: List[str] = []
    diagnostic_messages: List[str] = []
    ice_locations: List[Dict[str, Any]] = []
    compiler_paths: List[Dict[str, Any]] = []
    source_spans: List[Dict[str, Any]] = []

    for case in cases:
        if not isinstance(case, dict):
            continue
        output = str(case.get("output_excerpt") or "")
        output_class = classify_output(output, timed_out=bool(case.get("timed_out")))
        messages = extract_diagnostic_messages(output)
        case_ice_locations = extract_ice_locations(output)
        case_compiler_paths = extract_compiler_paths(output)
        case_source_spans = extract_source_spans(output)
        if messages or case_ice_locations or case_compiler_paths or case_source_spans or output_class not in {"other", "empty_output"}:
            diagnostics.append(
                {
                    "path": case.get("path", ""),
                    "language": case.get("language", ""),
                    "return_code": case.get("return_code"),
                    "timed_out": bool(case.get("timed_out")),
                    "class": output_class,
                    "messages": messages[:12],
                    "ice_locations": case_ice_locations[:8],
                    "compiler_paths_mentioned": case_compiler_paths[:8],
                    "source_spans": case_source_spans[:8],
                }
            )
        failure_kinds.extend(classify_failure_kinds(output))
        diagnostic_messages.extend(messages)
        ice_locations.extend(case_ice_locations)
        compiler_paths.extend(case_compiler_paths)
        source_spans.extend(case_source_spans)

    return {
        "instance_id": bug_id,
        "available": bool(cases),
        "availability": "available" if cases else "empty",
        "case_count": len(cases),
        "failure_kinds": unique_strings(failure_kinds)[:MAX_ITEMS],
        "diagnostic_messages": unique_strings(diagnostic_messages)[:MAX_ITEMS],
        "ice_locations": unique_dicts(ice_locations)[:MAX_ITEMS],
        "compiler_paths_mentioned": unique_dicts(compiler_paths)[:MAX_ITEMS],
        "source_spans": unique_dicts(source_spans)[:MAX_ITEMS],
        "diagnostics": diagnostics[:MAX_ITEMS],
    }


def empty_log(bug_id: str, *, availability: str) -> Dict[str, Any]:
    return {
        "instance_id": bug_id,
        "available": False,
        "availability": availability,
        "case_count": 0,
        "failure_kinds": [],
        "diagnostic_messages": [],
        "ice_locations": [],
        "compiler_paths_mentioned": [],
        "source_spans": [],
        "diagnostics": [],
    }


def extract_issue(issue_dir: Path, bug_id: str, *, max_chars: int) -> Dict[str, Any]:
    path = issue_dir / f"{bug_id}.json"
    obj = load_json_if_exists(path) or {}
    raw_text = str(obj.get("issue") or obj.get("bug_report") or obj.get("problem_statement") or obj.get("summary") or "")
    text = truncate(raw_text, max_chars)
    options = extract_options(text)
    compiler_paths = extract_compiler_paths(text)
    test_paths = unique_strings(TEST_PATH_RE.findall(text))
    ice_locations = extract_ice_locations(text)

    return {
        "instance_id": bug_id,
        "available": bool(raw_text.strip()),
        "summary": first_nonempty_line(text),
        "failure_kinds": classify_failure_kinds(text),
        "diagnostic_messages": extract_diagnostic_messages(text),
        "ice_locations": ice_locations,
        "during_passes": unique_strings(DURING_PASS_RE.findall(text)),
        "compiler_paths_mentioned": compiler_paths,
        "test_paths_mentioned": test_paths[:MAX_ITEMS],
        "target_triples": unique_strings(TARGET_TRIPLE_RE.findall(text))[:MAX_ITEMS],
        "target_arches": infer_target_arches(text, options, test_paths),
        "options": options[:MAX_ITEMS],
        "option_summary": summarize_options(options),
        "domain_tags": domain_tags(text, options, test_paths, []),
        "builtins": unique_strings(BUILTIN_RE.findall(text))[:MAX_ITEMS],
        "attributes": extract_attributes(text),
        "headers": unique_strings(INCLUDE_RE.findall(text))[:MAX_ITEMS],
    }


def extract_reproducer(reproducer_dir: Path, bug_id: str, *, max_chars: int) -> Dict[str, Any]:
    path = reproducer_dir / f"{bug_id}.json"
    obj = load_json_if_exists(path) or {}
    remaining = max_chars
    cases: List[Dict[str, Any]] = []
    languages: List[str] = []
    all_options: List[str] = []
    all_paths: List[str] = []
    all_domain_tags: List[str] = []
    all_builtins: List[str] = []
    all_attributes: List[str] = []
    all_headers: List[str] = []
    all_pragmas: List[str] = []
    all_functions: List[str] = []
    all_types: List[str] = []
    all_macros: List[str] = []
    all_expectations: List[str] = []

    for item in obj.get("test_cases") or []:
        record = item if isinstance(item, dict) else {}
        case_path = str(record.get("path") or "").strip()
        raw_content = str(record.get("content") or "")
        content = raw_content[:remaining] if remaining > 0 else ""
        remaining = max(0, remaining - len(content))
        language = infer_language(case_path)
        if language:
            languages.append(language)
        if case_path:
            all_paths.append(case_path)

        dg_options = extract_dg_options(content)
        options = unique_strings(extract_options(content) + dg_options + extract_target_standard_options(content))
        dg_do = unique_strings(clean_directive(item) for item in DG_DO_RE.findall(content))[:MAX_ITEMS]
        dg_final = unique_strings(clean_directive(item) for item in DG_FINAL_RE.findall(content))[:MAX_ITEMS]
        expectations = unique_strings(item.strip() for item in DG_EXPECT_RE.findall(content) if item.strip())[:MAX_ITEMS]
        headers = unique_strings(INCLUDE_RE.findall(content))[:MAX_ITEMS]
        builtins = unique_strings(BUILTIN_RE.findall(content))[:MAX_ITEMS]
        attributes = extract_attributes(content)
        pragmas = unique_strings(item.strip() for item in PRAGMA_RE.findall(content) if item.strip())[:MAX_ITEMS]
        function_names = extract_function_names(content)
        type_names = [name for name in unique_strings(TYPE_DECL_RE.findall(content)) if len(name) > 1][:MAX_ITEMS]
        macros = unique_strings(MACRO_RE.findall(content))[:MAX_ITEMS]
        tags = domain_tags(content, options, [case_path], [language])

        all_options.extend(options)
        all_domain_tags.extend(tags)
        all_builtins.extend(builtins)
        all_attributes.extend(attributes)
        all_headers.extend(headers)
        all_pragmas.extend(pragmas)
        all_functions.extend(function_names)
        all_types.extend(type_names)
        all_macros.extend(macros)
        all_expectations.extend(expectations)

        cases.append(
            {
                "path": case_path,
                "language": language,
                "test_family": test_family(case_path),
                "dg_do": dg_do,
                "dg_options": dg_options[:MAX_ITEMS],
                "dg_final": dg_final,
                "diagnostic_expectations": expectations,
                "options": options[:MAX_ITEMS],
                "target_arches": infer_target_arches(content, options, [case_path]),
                "domain_tags": tags,
                "builtins": builtins,
                "attributes": attributes,
                "headers": headers,
                "pragmas": pragmas,
                "function_names": function_names,
                "type_names": type_names,
                "macros": macros,
            }
        )

    options = unique_strings(all_options)
    paths = unique_strings(all_paths)

    return {
        "instance_id": bug_id,
        "available": bool(cases),
        "case_count": len(cases),
        "languages": unique_strings(languages),
        "test_paths": paths[:MAX_ITEMS],
        "test_families": unique_strings(test_family(path) for path in paths)[:MAX_ITEMS],
        "options": options[:MAX_ITEMS],
        "option_summary": summarize_options(options),
        "target_arches": infer_target_arches(" ".join(paths + options), options, paths),
        "domain_tags": unique_strings(all_domain_tags)[:MAX_ITEMS],
        "builtins": unique_strings(all_builtins)[:MAX_ITEMS],
        "attributes": unique_strings(all_attributes)[:MAX_ITEMS],
        "headers": unique_strings(all_headers)[:MAX_ITEMS],
        "pragmas": unique_strings(all_pragmas)[:MAX_ITEMS],
        "function_names": unique_strings(all_functions)[:MAX_ITEMS],
        "type_names": unique_strings(all_types)[:MAX_ITEMS],
        "macros": unique_strings(all_macros)[:MAX_ITEMS],
        "diagnostic_expectations": unique_strings(all_expectations)[:MAX_ITEMS],
        "during_passes": unique_strings(DURING_PASS_RE.findall("\n".join(paths + options)))[:MAX_ITEMS],
        "test_cases": cases[:MAX_ITEMS],
    }


def extract_diagnostic_messages(text: str) -> List[str]:
    messages: List[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if not stripped:
            continue
        if any(marker in lowered for marker in ("internal compiler error", "error:", "warning:", "fatal error:", "sorry,")):
            messages.append(collapse_space(stripped))
        elif "wrong code" in lowered or "rejects-valid" in lowered or "accepts-invalid" in lowered:
            messages.append(collapse_space(stripped))
        if len(messages) >= 20:
            break
    return unique_strings(messages)


def extract_ice_locations(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for match in ICE_RE.finditer(text):
        path = normalize_compiler_path(match.group("path"))
        function = str(match.group("function") or "").strip()
        line = int(match.group("line"))
        key = (function, path, line)
        if key in seen:
            continue
        seen.add(key)
        out.append({"function": function, "path": path, "line": line})
        if len(out) >= MAX_ITEMS:
            break
    return out


def extract_compiler_paths(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, int | None, int | None]] = set()
    for match in COMPILER_PATH_RE.finditer(text):
        path = normalize_compiler_path(match.group("path"))
        line = int(match.group("line")) if match.group("line") else None
        column = int(match.group("column")) if match.group("column") else None
        key = (path, line, column)
        if key in seen:
            continue
        seen.add(key)
        item: Dict[str, Any] = {"path": path}
        if line is not None:
            item["line"] = line
        if column is not None:
            item["column"] = column
        out.append(item)
        if len(out) >= MAX_ITEMS:
            break
    return out


def extract_source_spans(text: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in SOURCE_SPAN_RE.finditer(text):
        path = normalize_source_path(match.group("path"))
        line = int(match.group("line"))
        column = int(match.group("column"))
        key = (path, line, column)
        if key in seen:
            continue
        seen.add(key)
        out.append({"path": path, "line": line, "column": column})
        if len(out) >= MAX_ITEMS:
            break
    return out


def extract_options(text: str) -> List[str]:
    options = [normalize_option(match.group(1)) for match in OPTION_TOKEN_RE.finditer(text)]
    return filter_localization_options(options)


def extract_dg_options(content: str) -> List[str]:
    out: List[str] = []
    for text in DG_OPTIONS_RE.findall(content):
        out.extend(split_args(text))
    return unique_strings(out)


def extract_target_standard_options(content: str) -> List[str]:
    standards: List[str] = []
    for standard in TARGET_STANDARD_RE.findall(content):
        standards.append(f"-std={standard.lower()}")
    return unique_strings(standards)


def split_args(text: str) -> List[str]:
    text = text.strip()
    if not text:
        return []
    try:
        return [normalize_option(item) for item in shlex.split(text) if normalize_option(item)]
    except ValueError:
        return [normalize_option(item) for item in text.split() if normalize_option(item)]


def summarize_options(options: Iterable[Any]) -> Dict[str, List[str]]:
    items = filter_localization_options(options)
    return {
        "optimization_levels": [item for item in items if item.startswith("-O")][:MAX_ITEMS],
        "standards": [item for item in items if item.startswith("-std=")][:MAX_ITEMS],
        "target_options": [item for item in items if item.startswith("-m")][:MAX_ITEMS],
        "enabled_features": [
            item
            for item in items
            if item.startswith("-f") and not item.startswith("-fno-") and not item.startswith("-fdump")
        ][:MAX_ITEMS],
        "disabled_features": [item for item in items if item.startswith("-fno-")][:MAX_ITEMS],
        "warnings": [item for item in items if item.startswith("-W")][:MAX_ITEMS],
        "params": [item for item in items if item.startswith("--param=")][:MAX_ITEMS],
        "defines": [item for item in items if item.startswith("-D")][:MAX_ITEMS],
        "include_dirs": [item for item in items if item.startswith("-I")][:MAX_ITEMS],
    }


def classify_failure_kinds(text: str) -> List[str]:
    lowered = text.lower()
    kinds: List[str] = []
    checks = [
        ("ice", ("internal compiler error", " ice ", "[ice", "segmentation fault", "segfault")),
        ("wrong_code", ("wrong code", "miscompile", "incorrect code", "runtime failure")),
        ("rejects_valid", ("rejects-valid", "rejects valid", "should compile", "valid code rejected")),
        ("accepts_invalid", ("accepts-invalid", "accepts invalid", "should reject", "invalid code accepted")),
        ("diagnostic", ("warning:", "error:", "diagnostic", "dg-warning", "dg-error")),
        ("missed_optimization", ("missed optimization", "missed-optimization", "not optimized", "fails to optimize")),
        ("bootstrap_build", ("bootstrap", "build failure", "cannot build")),
        ("testsuite_regression", ("regression", "scan-assembler", "dg-final")),
    ]
    padded = f" {lowered} "
    for name, needles in checks:
        if any(needle in padded for needle in needles):
            kinds.append(name)
    return kinds[:MAX_ITEMS]


def domain_tags(text: str, options: Sequence[str], paths: Sequence[str], languages: Sequence[str]) -> List[str]:
    haystack = "\n".join([text, " ".join(options), " ".join(paths), " ".join(languages)]).lower()
    tags = [name for name, needles in DOMAIN_PATTERNS.items() if any(needle.lower() in haystack for needle in needles)]
    for arch in infer_target_arches(text, options, paths):
        tags.append(f"target:{arch}")
    for language in languages:
        if language and language != "unknown":
            tags.append(f"language:{language}")
    return unique_strings(tags)[:MAX_ITEMS]


def infer_target_arches(text: str, options: Sequence[str], paths: Sequence[str]) -> List[str]:
    lowered = "\n".join([text, " ".join(options), " ".join(paths)]).lower()
    arch_terms = {
        "x86": ("i386", "x86_64", "-msse", "-mavx", "-mmmx", "-march=x86"),
        "aarch64": ("aarch64", "armv8", "sve"),
        "arm": ("arm-", " arm", "thumb", "neon"),
        "riscv": ("riscv",),
        "powerpc": ("powerpc", "rs6000", "ppc"),
        "s390": ("s390",),
        "mips": ("mips",),
        "loongarch": ("loongarch",),
        "nvptx": ("nvptx",),
        "amdgcn": ("amdgcn",),
    }
    return [name for name, needles in arch_terms.items() if any(needle in lowered for needle in needles)]


def extract_attributes(text: str) -> List[str]:
    out: List[str] = []
    for match in ATTRIBUTE_RE.findall(text):
        for part in match.split(","):
            item = part.strip().split("(")[0].strip()
            if item:
                out.append(item)
    return unique_strings(out)[:MAX_ITEMS]


def extract_function_names(content: str) -> List[str]:
    names: List[str] = []
    for match in FUNCTION_DEF_RE.finditer(content):
        name = match.group("name")
        if name not in {"if", "for", "while", "switch", "return", "sizeof"}:
            names.append(name)
    return unique_strings(names)[:MAX_ITEMS]


def infer_language(path: str) -> str:
    suffix = Path(path).suffix
    if suffix in {".cc", ".cpp", ".cxx", ".C", ".ii"}:
        return "c++"
    if suffix in {".f", ".f90", ".f95", ".f03", ".f08", ".F", ".F90"}:
        return "fortran"
    if suffix in {".c", ".i"}:
        return "c"
    return suffix.lstrip(".") or "unknown"


def test_family(path: str) -> str:
    parts = Path(path).parts
    if "testsuite" not in parts:
        return ""
    index = parts.index("testsuite")
    family_parts = parts[index + 1 : -1]
    return "/".join(family_parts[:3])


def normalize_compiler_path(path: str) -> str:
    path = path.strip()
    if path.startswith("gcc/"):
        return path
    return f"gcc/{path}"


def normalize_source_path(path: str) -> str:
    path = path.strip()
    marker = "/gcc/testsuite/"
    if marker in path:
        return "gcc/testsuite/" + path.split(marker, 1)[1]
    if path.startswith("gcc/testsuite/"):
        return path
    return path


def normalize_option(option: str) -> str:
    return option.strip().strip("`'\".,;:")


def filter_localization_options(options: Iterable[Any]) -> List[str]:
    ignored = {"-o", "-c", "-S", "-E", "-fdump-passes"}
    ignored_prefixes = ("--prefix", "--enable-", "--disable-", "--with-", "--without-", "--build", "--host")
    return unique_strings(
        item
        for item in (normalize_option(str(option)) for option in options if str(option).strip())
        if item and item not in ignored and not item.startswith(ignored_prefixes)
    )


def clean_directive(text: str) -> str:
    return collapse_space(text.replace("{", " ").replace("}", " ").strip())


def first_nonempty_line(text: str, *, limit: int = 500) -> str:
    for line in text.splitlines():
        line = collapse_space(line)
        if line:
            return line[:limit]
    return ""


def collapse_space(text: str) -> str:
    return " ".join(text.split())


def unique_strings(items: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def unique_dicts(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = repr(sorted(item.items()))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def truncate(text: str, max_chars: int) -> str:
    return text if max_chars <= 0 else text[:max_chars]
