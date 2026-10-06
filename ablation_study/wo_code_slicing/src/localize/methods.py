from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List

from src.localize.repo import unique


FN_RE = re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)")
PARENT_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?P<kind>impl|trait)\b")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|E[0-9]{4}", re.IGNORECASE)
IDENT_BOUNDARY_CACHE: dict[str, re.Pattern[str]] = {}
FAILURE_MARKER_BONUS = 0.5
FAILURE_MARKERS = (
    "span_bug",
    "delay_span_bug",
    "bug!",
    "panic!",
    "assert!",
    "unreachable!",
    "todo!",
    "unwrap",
    "expect",
)

STOP_WORDS = {
    "about",
    "after",
    "again",
    "all",
    "also",
    "any",
    "are",
    "because",
    "before",
    "bin",
    "cannot",
    "com",
    "could",
    "crate",
    "crates",
    "error",
    "expected",
    "failed",
    "found",
    "for",
    "from",
    "had",
    "has",
    "have",
    "into",
    "issue",
    "known",
    "line",
    "main",
    "message",
    "note",
    "only",
    "rust",
    "rustc",
    "should",
    "source",
    "src",
    "sup",
    "the",
    "them",
    "than",
    "that",
    "their",
    "there",
    "this",
    "type",
    "value",
    "when",
    "where",
    "with",
    "compiler",
    "default",
    "use",
    "used",
    "uses",
    "using",
    "was",
    "were",
}
STOP_WORDS.update(
    {
        "__rust_begin_short_backtrace",
        "__rust_end_short_backtrace",
        "ablation_study",
        "appreciate",
        "backtrace",
        "backtracelock",
        "begin_panic",
        "bug",
        "collecting",
        "debug",
        "did",
        "does",
        "don",
        "dont",
        "during",
        "errors",
        "false",
        "home",
        "items",
        "private",
        "projects",
        "release",
        "run",
        "target",
        "tmp",
        "true",
        "users",
        "zhangtianrong",
    }
)


@dataclass(frozen=True)
class MethodCandidate:
    id: str
    file: str
    item_type: str
    item_name: str
    qualified_name: str
    signature: str
    parent_signature: str
    start_line: int
    end_line: int
    body: str
    body_view_kind: str = "full"
    body_original_chars: int = 0
    body_slice_terms: tuple[str, ...] = ()
    body_slice_ranges: tuple[str, ...] = ()

    def to_prompt_dict(self, *, include_body: bool = True) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": self.id,
            "file": self.file,
            "item_type": self.item_type,
            "item_name": self.item_name,
            "qualified_name": self.qualified_name,
            "signature": self.signature,
            "parent_signature": self.parent_signature,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if include_body:
            item["body"] = self.body
            item["body_view"] = {
                "kind": self.body_view_kind,
                "original_chars": self.body_original_chars or len(self.body),
                "selected_line_ranges": list(self.body_slice_ranges),
                "semantic_terms": list(self.body_slice_terms),
            }
        return item

    def to_report_dict(self, *, reason: str = "", rank: int | None = None) -> dict[str, Any]:
        item = self.to_prompt_dict(include_body=False)
        item["body_view"] = {
            "kind": self.body_view_kind,
            "original_chars": self.body_original_chars or len(self.body),
            "selected_line_ranges": list(self.body_slice_ranges),
            "semantic_terms": list(self.body_slice_terms),
        }
        if rank is not None:
            item["rank"] = rank
        if reason:
            item["reason"] = reason
        return item


class MethodStore:
    def __init__(
        self,
        *,
        rust_repo_dir: Path,
        max_body_chars: int = 12000,
        code_slicing_enabled: bool = False,
        slice_terms: Iterable[dict[str, Any] | str] = (),
        slice_min_body_chars: int = 2500,
        slice_context_lines: int = 8,
        slice_max_windows: int = 4,
    ) -> None:
        self.rust_repo_dir = rust_repo_dir
        self.max_body_chars = max_body_chars
        self.code_slicing_enabled = code_slicing_enabled
        self.slice_terms = normalize_slice_terms(slice_terms)
        self.slice_min_body_chars = slice_min_body_chars
        self.slice_context_lines = slice_context_lines
        self.slice_max_windows = slice_max_windows

    def load_methods_for_file(self, file_id: str) -> list[MethodCandidate]:
        path = self.rust_repo_dir / file_id
        if not path.is_file() or path.suffix != ".rs":
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        return extract_method_candidates(
            file_id=file_id,
            text=text,
            max_body_chars=self.max_body_chars,
            code_slicing_enabled=self.code_slicing_enabled,
            slice_terms=self.slice_terms,
            slice_min_body_chars=self.slice_min_body_chars,
            slice_context_lines=self.slice_context_lines,
            slice_max_windows=self.slice_max_windows,
        )

    def load_methods_for_files(self, file_ids: Iterable[str]) -> list[MethodCandidate]:
        methods: list[MethodCandidate] = []
        for file_id in unique(file_ids):
            methods.extend(self.load_methods_for_file(file_id))
        return methods


def extract_method_candidates(
    *,
    file_id: str,
    text: str,
    max_body_chars: int = 12000,
    code_slicing_enabled: bool = False,
    slice_terms: Iterable[dict[str, Any] | str] = (),
    slice_min_body_chars: int = 2500,
    slice_context_lines: int = 8,
    slice_max_windows: int = 4,
) -> list[MethodCandidate]:
    lines = text.splitlines()
    stripped = strip_comments_keep_lines(text).splitlines()
    methods: list[MethodCandidate] = []
    parent_stack: list[tuple[int, str, str]] = []
    depth = 0
    occupied: list[tuple[int, int]] = []
    normalized_terms = normalize_slice_terms(slice_terms)

    for index, line in enumerate(stripped, start=1):
        while parent_stack and depth < parent_stack[-1][0]:
            parent_stack.pop()

        if "{" in line:
            signature_lines, start_line = collect_signature_lines(stripped, index)
            signature = normalize_signature(" ".join(signature_lines))
            fn_match = FN_RE.search(signature)
            if fn_match and not is_inside(index, occupied):
                brace_col = line.find("{")
                end_line = find_body_end_line(stripped, index, brace_col)
                occupied.append((index, end_line))
                parent_signature = parent_stack[-1][2] if parent_stack else ""
                item_name = fn_match.group(1)
                item_type = "function"
                if parent_signature.strip().startswith("trait "):
                    item_type = "trait_method"
                elif parent_signature:
                    item_type = "method"
                qualified = f"{parent_signature}::{item_name}" if parent_signature else item_name
                body = "\n".join(lines[start_line - 1 : end_line])
                if code_slicing_enabled:
                    body_view = slice_method_body(
                        body=body,
                        start_line=start_line,
                        max_body_chars=max_body_chars,
                        min_body_chars=slice_min_body_chars,
                        slice_terms=normalized_terms,
                        context_lines=slice_context_lines,
                        max_windows=slice_max_windows,
                    )
                else:
                    body_view = {
                        "body": body[:max_body_chars],
                        "kind": "full",
                        "terms": [],
                        "ranges": [f"{start_line}-{end_line}"],
                    }
                methods.append(
                    MethodCandidate(
                        id=method_id(file_id=file_id, qualified_name=qualified, start_line=start_line, end_line=end_line),
                        file=file_id,
                        item_type=item_type,
                        item_name=item_name,
                        qualified_name=qualified,
                        signature=signature[:1200],
                        parent_signature=parent_signature,
                        start_line=start_line,
                        end_line=end_line,
                        body=str(body_view["body"]),
                        body_view_kind=str(body_view["kind"]),
                        body_original_chars=len(body),
                        body_slice_terms=tuple(str(item) for item in body_view["terms"]),
                        body_slice_ranges=tuple(str(item) for item in body_view["ranges"]),
                    )
                )

            parent_match = PARENT_RE.match(signature)
            if parent_match and not fn_match:
                parent_stack.append((depth + line.count("{") - line.count("}"), parent_match.group("kind"), strip_parent_signature(signature)))

        depth += count_braces(line)

    return methods


def collect_signature_lines(lines: List[str], line_index: int, *, max_lines: int = 30) -> tuple[list[str], int]:
    collected: list[str] = []
    start_line = line_index
    for previous in range(line_index, max(0, line_index - max_lines), -1):
        text = lines[previous - 1].strip()
        if not text and collected:
            break
        if text:
            collected.insert(0, text)
            start_line = previous
        if ("fn " in text or PARENT_RE.match(text)) and previous != line_index:
            break
    return collected, start_line


def find_body_end_line(lines: List[str], start_line: int, brace_column: int) -> int:
    depth = 0
    found_open = False
    for line_number in range(start_line, len(lines) + 1):
        line = lines[line_number - 1]
        start_col = brace_column if line_number == start_line else 0
        for char in line[start_col:]:
            if char == "{":
                depth += 1
                found_open = True
            elif char == "}":
                depth -= 1
                if found_open and depth <= 0:
                    return line_number
    return len(lines)


def strip_parent_signature(signature: str) -> str:
    signature = signature.split("{", 1)[0].strip()
    return signature.rstrip()


def method_id(*, file_id: str, qualified_name: str, start_line: int, end_line: int) -> str:
    return f"{file_id}::{qualified_name}@{start_line}-{end_line}"


def normalize_signature(signature: str) -> str:
    return " ".join(signature.replace("\t", " ").split())


def strip_comments_keep_lines(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    text = re.sub(r"/\*.*?\*/", repl, text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def count_braces(line: str) -> int:
    return line.count("{") - line.count("}")


def is_inside(line_number: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start < line_number <= end for start, end in ranges)


def normalize_slice_terms(slice_terms: Iterable[dict[str, Any] | str]) -> list[tuple[str, float]]:
    terms: OrderedDict[str, float] = OrderedDict()
    for item in slice_terms:
        if isinstance(item, dict):
            raw_term = str(item.get("term") or "").strip()
            raw_weight = item.get("weight", 1.0)
        else:
            raw_term = str(item or "").strip()
            raw_weight = 1.0
        term = normalize_term(raw_term)
        if not useful_term(term):
            continue
        try:
            weight = float(raw_weight)
        except (TypeError, ValueError):
            weight = 1.0
        terms[term] = max(terms.get(term, 0.0), weight)
    return list(terms.items())


def slice_method_body(
    *,
    body: str,
    start_line: int,
    max_body_chars: int,
    min_body_chars: int,
    slice_terms: Iterable[tuple[str, float]],
    context_lines: int,
    max_windows: int,
) -> dict[str, Any]:
    if len(body) <= min_body_chars:
        matched_terms = matched_terms_in_text(body, slice_terms)
        return {
            "body": body,
            "kind": "full",
            "terms": matched_terms[:20],
            "ranges": [f"{start_line}-{start_line + max(0, len(body.splitlines()) - 1)}"],
        }

    lines = body.splitlines()
    scored_lines = []
    for index, line in enumerate(lines):
        score, matched = score_line(line, slice_terms)
        if score <= 0:
            continue
        scored_lines.append((score, index, matched))

    if not scored_lines:
        if len(body) <= max_body_chars:
            return {
                "body": body,
                "kind": "full",
                "terms": [],
                "ranges": [f"{start_line}-{start_line + max(0, len(lines) - 1)}"],
            }
        return {
            "body": body[:max_body_chars],
            "kind": "truncated",
            "terms": [],
            "ranges": [f"{start_line}-{start_line + max(0, len(lines) - 1)}"],
        }

    selected = select_scored_windows(scored_lines=scored_lines, line_count=len(lines), context_lines=context_lines, max_windows=max_windows)

    if not selected:
        return {
            "body": body[:max_body_chars],
            "kind": "truncated",
            "terms": [],
            "ranges": [f"{start_line}-{start_line + max(0, len(lines) - 1)}"],
        }

    selected.sort(key=lambda item: item[0])
    merged = merge_windows(selected)
    snippet_body, ranges = render_sliced_body(lines=lines, start_line=start_line, windows=merged, max_body_chars=max_body_chars)
    terms = unique_terms(term for _begin, _end, _score, matched in merged for term in matched)
    return {
        "body": snippet_body,
        "kind": "sliced",
        "terms": terms[:20],
        "ranges": ranges,
    }


def score_line(line: str, slice_terms: Iterable[tuple[str, float]]) -> tuple[float, list[str]]:
    lowered = line.lower()
    token_set = line_token_set(lowered)
    normalized_line = normalize_term(lowered)
    score = 0.0
    matched: list[str] = []
    for term, weight in slice_terms:
        if term_matches_line(term, token_set, normalized_line):
            score += weight
            matched.append(term)
    for builtin in FAILURE_MARKERS:
        if builtin in lowered:
            score += FAILURE_MARKER_BONUS
            matched.append(builtin)
    return score, unique_terms(matched)


def matched_terms_in_text(text: str, slice_terms: Iterable[tuple[str, float]]) -> list[str]:
    lowered = text.lower()
    token_set = line_token_set(lowered)
    normalized_text = normalize_term(lowered)
    matched = [term for term, _weight in slice_terms if term_matches_line(term, token_set, normalized_text)]
    return unique_terms(matched)


def select_scored_windows(
    *,
    scored_lines: list[tuple[float, int, list[str]]],
    line_count: int,
    context_lines: int,
    max_windows: int,
) -> list[tuple[int, int, float, list[str]]]:
    line_scores = {index: (score, matched) for score, index, matched in scored_lines}
    candidates: dict[tuple[int, int], tuple[int, int, float, list[str]]] = {}
    for _score, index, _matched in scored_lines:
        begin = max(0, index - context_lines)
        end = min(line_count - 1, index + context_lines)
        window_score = 0.0
        terms: list[str] = []
        for line_index in range(begin, end + 1):
            item = line_scores.get(line_index)
            if item is None:
                continue
            line_score, line_terms = item
            window_score += line_score
            terms.extend(line_terms)
        matched_terms = unique_terms(terms)
        key = (begin, end)
        existing = candidates.get(key)
        if existing is None or window_score > existing[2]:
            candidates[key] = (begin, end, window_score, matched_terms)

    remaining = sorted(candidates.values(), key=lambda item: (-item[2], item[0], item[1]))
    selected: list[tuple[int, int, float, list[str]]] = []
    for begin, end, score, terms in remaining:
        if any(ranges_overlap(begin, end, selected_begin, selected_end) for selected_begin, selected_end, _score, _terms in selected):
            continue
        selected.append((begin, end, score, terms))
        if len(selected) >= max_windows:
            break

    selected.sort(key=lambda item: item[0])
    return selected


def ranges_overlap(begin: int, end: int, existing_begin: int, existing_end: int) -> bool:
    return begin <= existing_end and existing_begin <= end


def merge_windows(windows: list[tuple[int, int, float, list[str]]]) -> list[tuple[int, int, float, list[str]]]:
    merged: list[tuple[int, int, float, list[str]]] = []
    for begin, end, score, matched in windows:
        if not merged or begin > merged[-1][1] + 1:
            merged.append((begin, end, score, matched))
            continue
        prev_begin, prev_end, prev_score, prev_matched = merged[-1]
        merged[-1] = (
            prev_begin,
            max(prev_end, end),
            prev_score + score,
            unique_terms([*prev_matched, *matched]),
        )
    return merged


def render_sliced_body(
    *,
    lines: list[str],
    start_line: int,
    windows: list[tuple[int, int, float, list[str]]],
    max_body_chars: int,
) -> tuple[str, list[str]]:
    parts: list[str] = []
    ranges: list[str] = []
    for begin, end, _score, _matched in windows:
        absolute_begin = start_line + begin
        absolute_end = start_line + end
        ranges.append(f"{absolute_begin}-{absolute_end}")
        parts.append(f"// [code slice: lines {absolute_begin}-{absolute_end}]")
        parts.extend(lines[begin : end + 1])
    body = "\n".join(parts)
    while len(body) > max_body_chars and len(parts) > 2:
        parts = parts[:-2]
        body = "\n".join(parts)
    return body[:max_body_chars], ranges


def unique_terms(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        term = normalize_term(item)
        if term and term not in seen:
            seen.add(term)
            out.append(term)
    return out


def normalize_term(term: str) -> str:
    return " ".join(term.lower().replace("::", "_").replace("-", "_").split())


def useful_term(term: str) -> bool:
    if len(term) < 3 or term in STOP_WORDS:
        return False
    if re.fullmatch(r"[0-9a-f]{6,}", term):
        return False
    if re.fullmatch(r"bug_?[0-9]+", term):
        return False
    return bool(TOKEN_RE.search(term) or "_" in term)


def line_token_set(line: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in TOKEN_RE.findall(line):
        token = normalize_term(raw_token)
        if token:
            tokens.add(token)
            tokens.update(split_identifier_token(token))
    return tokens


def split_identifier_token(token: str) -> set[str]:
    parts = {part for part in re.split(r"_+", token) if part}
    for part in re.findall(r"[a-z]+|[0-9]+|E[0-9]{4}", token, flags=re.IGNORECASE):
        normalized = normalize_term(part)
        if normalized:
            parts.add(normalized)
    return {part for part in parts if useful_subtoken(part)}


def useful_subtoken(term: str) -> bool:
    return len(term) >= 3 and term not in STOP_WORDS and not re.fullmatch(r"[0-9a-f]{6,}", term) and not re.fullmatch(r"bug_?[0-9]+", term)


def term_matches_line(term: str, token_set: set[str], normalized_line: str) -> bool:
    if "_" not in term and " " not in term:
        return term in token_set
    return bool(identifier_boundary_pattern(term).search(normalized_line))


def identifier_boundary_pattern(term: str) -> re.Pattern[str]:
    pattern = IDENT_BOUNDARY_CACHE.get(term)
    if pattern is None:
        pattern = re.compile(rf"(?<![a-z0-9_]){re.escape(term)}(?![a-z0-9_])")
        IDENT_BOUNDARY_CACHE[term] = pattern
    return pattern
