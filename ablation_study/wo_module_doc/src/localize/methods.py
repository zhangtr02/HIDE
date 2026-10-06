from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List

from src.localize.repo import unique


FN_RE = re.compile(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)")
PARENT_RE = re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?P<kind>impl|trait)\b")


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
    def __init__(self, *, rust_repo_dir: Path, max_body_chars: int = 12000) -> None:
        self.rust_repo_dir = rust_repo_dir
        self.max_body_chars = max_body_chars

    def load_methods_for_file(self, file_id: str) -> list[MethodCandidate]:
        path = self.rust_repo_dir / file_id
        if not path.is_file() or path.suffix != ".rs":
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        return extract_method_candidates(file_id=file_id, text=text, max_body_chars=self.max_body_chars)

    def load_methods_for_files(self, file_ids: Iterable[str]) -> list[MethodCandidate]:
        methods: list[MethodCandidate] = []
        for file_id in unique(file_ids):
            methods.extend(self.load_methods_for_file(file_id))
        return methods


def extract_method_candidates(*, file_id: str, text: str, max_body_chars: int = 12000) -> list[MethodCandidate]:
    lines = text.splitlines()
    stripped = strip_comments_keep_lines(text).splitlines()
    methods: list[MethodCandidate] = []
    parent_stack: list[tuple[int, str, str]] = []
    depth = 0
    occupied: list[tuple[int, int]] = []

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
                        body=body[:max_body_chars],
                        body_original_chars=len(body),
                        body_slice_ranges=(f"{start_line}-{end_line}",),
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
