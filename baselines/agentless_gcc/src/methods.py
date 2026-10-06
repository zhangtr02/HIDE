from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List

from .io_utils import unique_keep_order


CODE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inc"}
CONTROL_KEYWORDS = {
    "if",
    "for",
    "while",
    "switch",
    "catch",
    "return",
    "sizeof",
    "alignof",
    "static_assert",
}
NAME_RE = re.compile(
    r"(?P<name>(?:[A-Za-z_][A-Za-z0-9_]*::)*~?[A-Za-z_][A-Za-z0-9_]*|operator\s*[^\s(]+)\s*\("
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
        return item

    def to_report_dict(self, *, reason: str = "", source: str = "") -> dict[str, Any]:
        item = self.to_prompt_dict(include_body=False)
        if reason:
            item["reason"] = reason
        if source:
            item["source"] = source
        return item


class MethodStore:
    def __init__(self, *, gcc_repo_dir: Path, max_body_chars: int = 12000) -> None:
        self.gcc_repo_dir = gcc_repo_dir
        self.max_body_chars = max_body_chars

    def load_methods_for_files(self, file_ids: Iterable[str]) -> list[MethodCandidate]:
        methods: list[MethodCandidate] = []
        for file_id in unique_keep_order(file_ids):
            methods.extend(self.load_methods_for_file(file_id))
        return methods

    def load_methods_for_file(self, file_id: str) -> list[MethodCandidate]:
        path = self.gcc_repo_dir / file_id
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            return []
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return []
        return extract_method_candidates(file_id=file_id, text=text, max_body_chars=self.max_body_chars)


def extract_method_candidates(*, file_id: str, text: str, max_body_chars: int = 12000) -> list[MethodCandidate]:
    lines = text.splitlines()
    cleaned_lines = strip_block_comments_keep_lines(text).splitlines()
    methods: list[MethodCandidate] = []
    occupied_ranges: list[tuple[int, int]] = []

    for line_index, line in enumerate(cleaned_lines, start=1):
        brace_column = line.find("{")
        if brace_column < 0:
            continue
        signature_lines, start_line = collect_signature_lines(cleaned_lines, line_index, brace_column)
        signature = normalize_signature(" ".join(signature_lines))
        if not looks_like_function_signature(signature):
            continue
        if is_inside_existing_range(line_index, occupied_ranges):
            continue
        name = extract_function_name(signature)
        if not name:
            continue
        end_line = find_body_end_line(cleaned_lines, line_index, brace_column)
        if end_line < line_index:
            continue
        occupied_ranges.append((line_index, end_line))
        body = "\n".join(lines[start_line - 1 : end_line])
        methods.append(
            MethodCandidate(
                id=method_id(file_id=file_id, qualified_name=name, start_line=start_line, end_line=end_line),
                file=file_id,
                item_type="method" if "::" in name else "function",
                item_name=name.split("::")[-1].strip(),
                qualified_name=name,
                signature=signature[:1200],
                parent_signature="",
                start_line=start_line,
                end_line=end_line,
                body=body[:max_body_chars],
            )
        )
    return methods


def method_at_line(methods: Iterable[MethodCandidate], file_id: str, line_number: int) -> MethodCandidate | None:
    candidates = [
        method
        for method in methods
        if method.file == file_id and method.start_line <= line_number <= method.end_line
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda method: method.end_line - method.start_line)


def collect_signature_lines(lines: List[str], line_index: int, brace_column: int, *, max_lines: int = 20) -> tuple[list[str], int]:
    current_prefix = lines[line_index - 1][:brace_column].strip()
    collected: list[str] = [current_prefix] if current_prefix else []
    start_line = line_index
    for previous in range(line_index - 1, max(0, line_index - max_lines), -1):
        text = lines[previous - 1].strip()
        if not text:
            break
        if text.endswith(";") or text.endswith("}") or text.endswith("{"):
            break
        collected.insert(0, text)
        start_line = previous
        if "(" in text and not text.startswith(("*", "&")):
            continue
    return collected, start_line


def looks_like_function_signature(signature: str) -> bool:
    if not signature or "(" not in signature or ")" not in signature:
        return False
    if signature.startswith("#") or signature.startswith("typedef "):
        return False
    if signature.endswith("=") or signature.endswith(":"):
        return False
    name = extract_function_name(signature)
    if not name:
        return False
    bare = name.split("::")[-1].replace("operator", "operator").strip()
    return bare not in CONTROL_KEYWORDS


def extract_function_name(signature: str) -> str:
    prefix = signature.rsplit(")", 1)[0]
    matches = list(NAME_RE.finditer(prefix))
    if not matches:
        return ""
    name = " ".join(matches[-1].group("name").split())
    if name.split("::")[-1] in CONTROL_KEYWORDS:
        return ""
    return name


def find_body_end_line(lines: List[str], start_line: int, brace_column: int) -> int:
    depth = 0
    found_open = False
    for line_number in range(start_line, len(lines) + 1):
        line = strip_line_comment(lines[line_number - 1])
        start_col = brace_column if line_number == start_line else 0
        index = start_col
        while index < len(line):
            char = line[index]
            if char == "{":
                found_open = True
                depth += 1
            elif char == "}":
                depth -= 1
                if found_open and depth <= 0:
                    return line_number
            index += 1
    return len(lines)


def method_id(*, file_id: str, qualified_name: str, start_line: int, end_line: int) -> str:
    return f"{file_id}::{qualified_name}@{start_line}-{end_line}"


def normalize_signature(signature: str) -> str:
    return " ".join(signature.replace("\t", " ").split())


def strip_block_comments_keep_lines(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return re.sub(r"/\*.*?\*/", repl, text, flags=re.DOTALL)


def strip_line_comment(line: str) -> str:
    return re.sub(r"//.*", "", line)


def is_inside_existing_range(line_number: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start < line_number <= end for start, end in ranges)
