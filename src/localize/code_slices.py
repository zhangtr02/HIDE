from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|E[0-9]{4}", re.IGNORECASE)
FAILURE_MARKERS = ("span_bug", "delay_span_bug", "bug!", "panic!", "assert!", "unreachable!", "unwrap", "expect(")
STOP_WORDS = {
    "about", "after", "also", "been", "cannot", "compiler", "crate", "crates",
    "default", "error", "expected", "failed", "file", "files", "found", "from",
    "have", "into", "issue", "line", "main", "message", "only", "rust", "rustc",
    "should", "source", "that", "their", "there", "these", "this", "type",
    "value", "when", "where", "with", "would", "src", "target", "debug",
}


def build_code_slice_terms(
    *, evidence: dict[str, Any], issue: dict[str, Any], reproducer: dict[str, Any], max_terms: int
) -> list[dict[str, Any]]:
    terms: dict[str, dict[str, Any]] = {}

    def add(value: Any, *, weight: float, source: str) -> None:
        for text in flatten_text(value):
            for raw in TOKEN_RE.findall(text):
                term = raw.lower()
                if not useful_term(term):
                    continue
                previous = terms.get(term)
                if previous is None or weight > previous["weight"]:
                    terms[term] = {"term": term, "weight": weight, "source": source}

    log = evidence.get("log") if isinstance(evidence.get("log"), dict) else {}
    trace = evidence.get("query_trace") if isinstance(evidence.get("query_trace"), dict) else {}
    for key in ("ice_message", "panic_messages", "query_stack", "rustc_symbols", "compiler_paths_mentioned", "diagnostic_messages"):
        add(log.get(key), weight=2.0, source=f"log.{key}")
    for key in ("queries", "query_key_terms", "query_key_samples"):
        add(trace.get(key), weight=2.0, source=f"query_trace.{key}")
    add(issue.get("title"), weight=1.0, source="issue.title")
    add(issue.get("body"), weight=1.0, source="issue.body")
    add(reproducer.get("content"), weight=1.0, source="reproducer.content")

    high = sorted((item for item in terms.values() if item["weight"] == 2.0), key=term_sort_key)
    low = sorted((item for item in terms.values() if item["weight"] == 1.0), key=term_sort_key)
    budget = max(0, max_terms)
    low_budget = min(len(low), budget // 4) if high else budget
    selected = high[: budget - low_budget] + low[:low_budget]
    if len(selected) < budget:
        selected.extend(high[budget - low_budget : budget - low_budget + budget - len(selected)])
    if len(selected) < budget:
        selected.extend(low[low_budget : low_budget + budget - len(selected)])
    return selected


def flatten_text(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from flatten_text(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from flatten_text(item)


def useful_term(term: str) -> bool:
    return len(term) >= 3 and term not in STOP_WORDS and not re.fullmatch(r"[0-9a-f]{6,}|bug_?[0-9]+", term)


def term_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    return (-len(item["term"]), item["term"])


@dataclass(frozen=True)
class FileCodeSlice:
    id: str
    original_chars: int
    score: float
    hit_count: int
    matched_terms: tuple[str, ...]
    selected_line_ranges: tuple[str, ...]
    snippet: str

    def to_prompt_dict(self) -> dict[str, Any]:
        return {"id": self.id, "code_view": {
            "kind": "sliced", "original_chars": self.original_chars,
            "score": round(self.score, 3), "hit_count": self.hit_count,
            "selected_line_ranges": list(self.selected_line_ranges),
            "semantic_terms": list(self.matched_terms), "snippets": [self.snippet],
        }}

    def to_report_dict(self) -> dict[str, Any]:
        return self.to_prompt_dict()


class FileCodeSliceStore:
    def __init__(
        self, *, rust_repo_dir: Path, slice_terms: Iterable[dict[str, Any]],
        context_lines: int = 4, max_windows: int = 2,
        max_chars_per_file: int = 1200, max_files_per_scope: int = 12,
    ) -> None:
        self.rust_repo_dir = rust_repo_dir
        self.slice_terms = [(str(item["term"]).lower(), float(item["weight"])) for item in slice_terms]
        self.context_lines = max(0, context_lines)
        self.max_windows = max(1, max_windows)
        self.max_chars_per_file = max(1, max_chars_per_file)
        self.max_files_per_scope = max_files_per_scope
        self._cache: dict[str, FileCodeSlice | None] = {}

    def load_file_slices(self, file_ids: Iterable[str]) -> list[FileCodeSlice]:
        if not self.slice_terms:
            return []
        slices = [item for file_id in dict.fromkeys(file_ids) if (item := self.load_file_slice(file_id)) is not None]
        slices.sort(key=lambda item: (-item.score, -item.hit_count, item.id))
        return slices[: self.max_files_per_scope] if self.max_files_per_scope > 0 else slices

    def load_file_slice(self, file_id: str) -> FileCodeSlice | None:
        if file_id not in self._cache:
            path = (self.rust_repo_dir / file_id).resolve()
            root = self.rust_repo_dir.resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.suffix != ".rs":
                self._cache[file_id] = None
            else:
                self._cache[file_id] = slice_file_code(
                    file_id=file_id, text=path.read_text(encoding="utf-8", errors="replace"),
                    slice_terms=self.slice_terms, context_lines=self.context_lines,
                    max_windows=self.max_windows, max_chars_per_file=self.max_chars_per_file,
                )
        return self._cache[file_id]


def slice_file_code(
    *, file_id: str, text: str, slice_terms: Iterable[tuple[str, float]],
    context_lines: int, max_windows: int, max_chars_per_file: int,
) -> FileCodeSlice | None:
    lines = text.splitlines()
    terms = list(slice_terms)
    line_scores: list[float] = []
    line_matches: list[list[str]] = []
    for line in lines:
        tokens = {token.lower() for token in TOKEN_RE.findall(line)}
        matched = [term for term, _weight in terms if term in tokens]
        weighted = sum(weight for term, weight in terms if term in tokens)
        bonus = 0.5 if any(marker in line.lower() for marker in FAILURE_MARKERS) else 0.0
        line_scores.append(weighted + bonus)
        line_matches.append(matched)
    hit_lines = [index for index, matched in enumerate(line_matches) if matched]
    if not hit_lines:
        return None

    windows: dict[tuple[int, int], tuple[float, int]] = {}
    for center in hit_lines:
        begin, end = max(0, center - context_lines), min(len(lines) - 1, center + context_lines)
        score = sum(line_scores[begin : end + 1])
        previous = windows.get((begin, end))
        if previous is None or score > previous[0]:
            windows[(begin, end)] = (score, center)

    ranked = sorted(((score, begin, end, center) for (begin, end), (score, center) in windows.items()), key=lambda item: (-item[0], item[1]))
    selected: list[tuple[int, int, str]] = []
    remaining = max_chars_per_file
    for _score, begin, end, center in ranked:
        if any(begin <= other_end and other_begin <= end for other_begin, other_end, _ in selected):
            continue
        rendered = render_window(lines, begin=begin, end=end, center=center, budget=remaining)
        if rendered is None:
            continue
        actual_begin, actual_end, snippet = rendered
        selected.append((actual_begin, actual_end, snippet))
        remaining -= len(snippet) + 2
        if len(selected) >= max_windows or remaining < 40:
            break
    if not selected:
        return None

    selected.sort(key=lambda item: item[0])
    matched_terms = sorted({term for begin, end, _ in selected for matched in line_matches[begin : end + 1] for term in matched})
    score = sum(sum(line_scores[begin : end + 1]) for begin, end, _ in selected)
    return FileCodeSlice(
        id=file_id, original_chars=len(text), score=score, hit_count=len(hit_lines),
        matched_terms=tuple(matched_terms[:20]),
        selected_line_ranges=tuple(f"{begin + 1}-{end + 1}" for begin, end, _ in selected),
        snippet="\n\n".join(snippet for _begin, _end, snippet in selected),
    )


def render_window(
    lines: list[str], *, begin: int, end: int, center: int, budget: int
) -> tuple[int, int, str] | None:
    if budget < 40:
        return None
    while begin < end:
        header = f"// [code slice: lines {begin + 1}-{end + 1}]\n"
        if len(header) + len("\n".join(lines[begin : end + 1])) <= budget:
            break
        if center - begin >= end - center and begin < center:
            begin += 1
        elif end > center:
            end -= 1
        else:
            break
    header = f"// [code slice: lines {begin + 1}-{end + 1}]\n"
    room = budget - len(header)
    if room <= 0:
        return None
    body = "\n".join(lines[begin : end + 1])[:room]
    return begin, end, header + body
