from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List

from src.common.json_io import load_json_if_exists, write_json


ERROR_CODE_RE = re.compile(r"\bE\d{4}\b")
DIAGNOSTIC_RE = re.compile(r"^(?:error|warning)(?:\[[A-Z]\d{4}\])?:\s*(.+)$", re.MULTILINE)
ICE_RE = re.compile(r"error:\s*internal compiler error:\s*(.+)")
PANIC_RE = re.compile(r"(?:thread 'rustc' panicked at|panicked at)\s+([^\n]+)")
COMPILER_PATH_RE = re.compile(
    r"(?P<path>compiler/[\w_/-]+\.rs)(?::(?P<line>\d+))?(?::(?P<column>\d+))?"
)
SOURCE_SPAN_RE = re.compile(r"-->\s+(?P<path>[\w_./-]+\.rs):(?P<line>\d+):(?P<column>\d+)")
QUERY_STACK_RE = re.compile(r"^\s*#(?P<index>\d+)\s+\[(?P<query>[A-Za-z0-9_:-]+)\]\s*(?P<description>.*)$", re.MULTILINE)
RUST_SYMBOL_RE = re.compile(r"\brustc_[A-Za-z0-9_]+(?:\[[^\]]+\])?(?:::[:A-Za-z0-9_#<>.-]+)+")


class EvidenceExtractor:
    def __init__(self, *, log_dir: Path, query_trace_dir: Path) -> None:
        self.log_dir = log_dir
        self.query_trace_dir = query_trace_dir

    def extract(self, *, bug_id: str, toolchain: str = "", build_args: str = "") -> Dict[str, Any]:
        log_text = read_text_if_exists(self.log_dir / f"{bug_id}.log")
        query_trace = extract_query_trace(self.query_trace_dir, bug_id=bug_id)
        evidence = {
            "bug_id": bug_id,
            "execution": extract_execution(log_text, toolchain=toolchain, build_args=build_args),
            "log": extract_log(log_text),
            "query_trace": query_trace,
        }
        return evidence


def extract_issue(issue_dir: Path, bug_id: str, *, max_chars: int = 24000) -> Dict[str, Any]:
    obj = load_json_if_exists(issue_dir / f"{bug_id}.json") or {}
    title = str(obj.get("title") or "").strip()
    labels = obj.get("labels") if isinstance(obj.get("labels"), list) else []
    body = str(obj.get("body") or "").strip()
    return {
        "bug_id": bug_id,
        "title": title,
        "labels": [str(item) for item in labels if str(item).strip()],
        "body": truncate(body, max_chars),
    }


def extract_reproducer(dataset_dir: Path, bug_id: str, *, max_chars: int = 24000) -> Dict[str, Any]:
    root = dataset_dir / bug_id
    files: List[Dict[str, str]] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".git" in path.parts:
                continue
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.name
            if rel in {"Cargo.lock"}:
                continue
            if path.suffix not in {".rs", ".toml", ".txt"} and path.name not in {"rust-toolchain", "rust-toolchain.toml"}:
                continue
            text = read_text_if_exists(path)
            files.append({"path": rel, "content": truncate(text, max_chars // 2)})
    total = "\n\n".join(f"// {item['path']}\n{item['content']}" for item in files)
    return {
        "bug_id": bug_id,
        "root": str(root),
        "files": files[:12],
        "content": truncate(total, max_chars),
    }


def extract_execution(log_text: str, *, toolchain: str, build_args: str) -> Dict[str, Any]:
    explicit_toolchain = match_line_value(log_text, "Toolchain") or toolchain
    command = match_line_value(log_text, "CMD")
    return_code = 101 if "internal compiler error" in log_text or "panicked at" in log_text else None
    if "error:" in log_text and return_code is None:
        return_code = 1
    return {
        "toolchain": explicit_toolchain,
        "return_code": return_code,
        "kind": classify_log(log_text),
        "command": command,
        "build_args": build_args,
        "rustflags": extract_rustflags(log_text + "\n" + build_args),
    }


def extract_log(log_text: str) -> Dict[str, Any]:
    return {
        "error_codes": unique(ERROR_CODE_RE.findall(log_text)),
        "diagnostic_messages": unique(match.strip() for match in DIAGNOSTIC_RE.findall(log_text))[:24],
        "ice_message": first_match(ICE_RE, log_text),
        "panic_messages": unique(PANIC_RE.findall(log_text))[:12],
        "panic_modes": panic_modes(log_text),
        "query_stack": extract_query_stack(log_text),
        "compiler_paths_mentioned": extract_compiler_paths(log_text),
        "source_spans": extract_source_spans(log_text),
        "rustc_symbols": unique(RUST_SYMBOL_RE.findall(log_text))[:24],
        "raw_tail": log_text[-8000:],
    }


def extract_query_trace(query_trace_dir: Path, *, bug_id: str) -> Dict[str, Any]:
    trace_path = query_trace_dir / bug_id / f"{bug_id}.query_trace.json"
    summary_path = query_trace_dir / bug_id / f"{bug_id}.json"
    trace = load_json_if_exists(trace_path) or {}
    summary = load_json_if_exists(summary_path) or {}
    trace_items = trace.get("trace") if isinstance(trace.get("trace"), list) else []
    summary_obj = trace.get("summary") if isinstance(trace.get("summary"), dict) else {}
    query_data = summary.get("query_data") if isinstance(summary.get("query_data"), list) else []
    query_names: List[str] = []
    query_names.extend(str(item.get("query") or item.get("label") or "").strip() for item in trace_items if isinstance(item, dict))
    query_names.extend(str(item.get("label") or "").strip() for item in query_data if isinstance(item, dict))
    summary_queries = summary_obj.get("queries")
    if isinstance(summary_queries, list):
        query_names.extend(str(item).strip() for item in summary_queries)
    queries = unique(query_names)
    key_samples = summary_obj.get("query_key_samples") if isinstance(summary_obj.get("query_key_samples"), list) else []
    key_terms = summary_obj.get("query_key_terms") if isinstance(summary_obj.get("query_key_terms"), list) else []
    return {
        "available": bool(trace or summary),
        "status": trace.get("status", "missing" if not trace and not summary else "ok"),
        "queries": [item for item in queries if item and item != "<unknown>"][:80],
        "query_key_samples": key_samples[:40],
        "query_key_terms": key_terms[:80],
        "summary": summary_obj,
    }


def save_evidence(evidence_dir: Path, evidence: Dict[str, Any]) -> None:
    bug_id = str(evidence.get("bug_id") or "").strip()
    if bug_id:
        write_json(evidence_dir / f"{bug_id}.json", evidence)


def extract_query_stack(log_text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for match in QUERY_STACK_RE.finditer(log_text):
        rows.append(
            {
                "index": int(match.group("index")),
                "query": match.group("query"),
                "description": match.group("description").strip(),
            }
        )
    return rows[:40]


def extract_compiler_paths(log_text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[tuple[str, int | None, int | None]] = set()
    for match in COMPILER_PATH_RE.finditer(log_text):
        line = int(match.group("line")) if match.group("line") else None
        column = int(match.group("column")) if match.group("column") else None
        key = (match.group("path"), line, column)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"path": key[0], "line": line, "column": column})
    return rows[:40]


def extract_source_spans(log_text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in SOURCE_SPAN_RE.finditer(log_text):
        key = (match.group("path"), int(match.group("line")), int(match.group("column")))
        if key in seen:
            continue
        seen.add(key)
        rows.append({"path": key[0], "line": key[1], "column": key[2]})
    return rows[:40]


def panic_modes(log_text: str) -> List[str]:
    modes: List[str] = []
    if "internal compiler error" in log_text:
        modes.append("internal_compiler_error")
    if "query stack during panic" in log_text or "query_stack" in log_text:
        modes.append("query_stack")
    if "stack backtrace:" in log_text:
        modes.append("backtrace")
    return modes


def classify_log(log_text: str) -> str:
    if "internal compiler error" in log_text or "panicked at" in log_text:
        return "ice_or_panic"
    if "error:" in log_text:
        return "compile_error"
    if log_text.strip():
        return "other"
    return "missing"


def extract_rustflags(text: str) -> List[str]:
    return unique(re.findall(r"-Z[\w-]+(?:=[^\s,;]+)?|-C[\w-]+(?:=[^\s,;]+)?", text))[:40]


def match_line_value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def first_match(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
