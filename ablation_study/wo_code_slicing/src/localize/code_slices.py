from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from src.localize.methods import merge_windows, normalize_slice_terms, render_sliced_body, score_line, select_scored_windows, unique_terms
from src.localize.repo import unique


@dataclass(frozen=True)
class FileCodeSlice:
    id: str
    view_kind: str
    original_chars: int
    score: float
    hit_count: int
    matched_terms: tuple[str, ...]
    selected_line_ranges: tuple[str, ...]
    snippet: str

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code_view": {
                "kind": self.view_kind,
                "original_chars": self.original_chars,
                "score": round(self.score, 3),
                "hit_count": self.hit_count,
                "selected_line_ranges": list(self.selected_line_ranges),
                "semantic_terms": list(self.matched_terms),
                "snippets": [self.snippet] if self.snippet else [],
            },
        }

    def to_report_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code_view": {
                "kind": self.view_kind,
                "original_chars": self.original_chars,
                "score": round(self.score, 3),
                "hit_count": self.hit_count,
                "selected_line_ranges": list(self.selected_line_ranges),
                "semantic_terms": list(self.matched_terms),
            },
        }


class FileCodeSliceStore:
    def __init__(
        self,
        *,
        rust_repo_dir: Path,
        enabled: bool,
        slice_terms: Iterable[dict[str, Any] | str],
        context_lines: int = 4,
        max_windows: int = 2,
        max_chars_per_file: int = 1200,
        max_files_per_scope: int = 12,
    ) -> None:
        self.rust_repo_dir = rust_repo_dir
        self.enabled = enabled
        self.slice_terms = normalize_slice_terms(slice_terms)
        self.context_lines = context_lines
        self.max_windows = max_windows
        self.max_chars_per_file = max_chars_per_file
        self.max_files_per_scope = max_files_per_scope

    def load_file_slices(self, file_ids: Iterable[str]) -> list[FileCodeSlice]:
        if not self.enabled or not self.slice_terms:
            return []
        slices: list[FileCodeSlice] = []
        for file_id in unique(file_ids):
            item = self.load_file_slice(file_id)
            if item.hit_count > 0 and item.snippet:
                slices.append(item)
        slices.sort(key=lambda item: (-item.score, -item.hit_count, item.id))
        if self.max_files_per_scope > 0:
            return slices[: self.max_files_per_scope]
        return slices

    def load_file_slice(self, file_id: str) -> FileCodeSlice:
        path = self.rust_repo_dir / file_id
        if not path.is_file() or path.suffix != ".rs":
            return empty_file_slice(file_id)
        text = path.read_text(encoding="utf-8", errors="replace")
        return slice_file_code(
            file_id=file_id,
            text=text,
            slice_terms=self.slice_terms,
            context_lines=self.context_lines,
            max_windows=self.max_windows,
            max_chars_per_file=self.max_chars_per_file,
        )


def slice_file_code(
    *,
    file_id: str,
    text: str,
    slice_terms: Iterable[tuple[str, float]],
    context_lines: int,
    max_windows: int,
    max_chars_per_file: int,
) -> FileCodeSlice:
    lines = text.splitlines()
    scored_lines: list[tuple[float, int, list[str]]] = []
    for index, line in enumerate(lines):
        score, matched = score_line(line, slice_terms)
        if score <= 0:
            continue
        scored_lines.append((score, index, matched))

    if not scored_lines:
        return empty_file_slice(file_id, original_chars=len(text))

    selected = select_scored_windows(scored_lines=scored_lines, line_count=len(lines), context_lines=context_lines, max_windows=max_windows)

    if not selected:
        return empty_file_slice(file_id, original_chars=len(text))

    selected.sort(key=lambda item: item[0])
    merged = merge_windows(selected)
    snippet, ranges = render_sliced_body(lines=lines, start_line=1, windows=merged, max_body_chars=max_chars_per_file)
    terms = unique_terms(term for _begin, _end, _score, matched in merged for term in matched)
    return FileCodeSlice(
        id=file_id,
        view_kind="sliced",
        original_chars=len(text),
        score=sum(score for _begin, _end, score, _matched in merged),
        hit_count=len(scored_lines),
        matched_terms=tuple(terms[:20]),
        selected_line_ranges=tuple(ranges),
        snippet=snippet,
    )


def empty_file_slice(file_id: str, *, original_chars: int = 0) -> FileCodeSlice:
    return FileCodeSlice(
        id=file_id,
        view_kind="empty",
        original_chars=original_chars,
        score=0.0,
        hit_count=0,
        matched_terms=(),
        selected_line_ranges=(),
        snippet="",
    )
