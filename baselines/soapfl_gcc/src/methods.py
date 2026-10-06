from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, List

from .io_utils import unique_keep_order, write_json
from .repo import CODE_SUFFIXES, clean_comment, is_meaningful_comment


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
MACRO_LIKE_NAMES = {
    "GTY",
}
NAME_RE = re.compile(
    r"(?P<name>(?:[A-Za-z_][A-Za-z0-9_]*::)*~?[A-Za-z_][A-Za-z0-9_]*|operator\s*[^\s(]+)\s*\("
)
CALL_RE = re.compile(r"\b((?:[A-Za-z_][A-Za-z0-9_]*::)*[A-Za-z_][A-Za-z0-9_]*)\s*\(")


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
    summary: str
    call_list: List[str]

    def to_prompt_dict(self, *, include_body: bool = True, body_chars: int | None = None) -> dict[str, Any]:
        item: dict[str, Any] = {
            "id": self.id,
            "file": self.file,
            "item_type": self.item_type,
            "item_name": self.item_name,
            "qualified_name": self.qualified_name,
            "signature": self.signature,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "summary": self.summary,
            "calls": self.call_list,
        }
        if include_body:
            item["body"] = self.body if body_chars is None else self.body[:body_chars]
        return item

    def to_report_dict(self, *, reason: str = "", score: int | None = None, source: str = "") -> dict[str, Any]:
        item = self.to_prompt_dict(include_body=False)
        if reason:
            item["reason"] = reason
        if score is not None:
            item["score"] = score
        if source:
            item["source"] = source
        return item


class MethodStore:
    def __init__(self, *, gcc_repo_dir: Path, docs_method_dir: Path, max_body_chars: int = 12000) -> None:
        self.gcc_repo_dir = gcc_repo_dir
        self.docs_method_dir = docs_method_dir
        self.max_body_chars = max_body_chars

    def load_methods_for_file(self, file_id: str) -> list[MethodCandidate]:
        path = self.gcc_repo_dir / file_id
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        methods = extract_method_candidates(file_id=file_id, text=text, max_body_chars=self.max_body_chars)
        for method in methods:
            if method.summary:
                write_json(method_doc_path(self.docs_method_dir, method), method_doc(method))
        return methods

    def load_methods_for_files(self, file_ids: Iterable[str]) -> list[MethodCandidate]:
        methods: list[MethodCandidate] = []
        for file_id in unique_keep_order(file_ids):
            methods.extend(self.load_methods_for_file(file_id))
        return methods


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
        summary = extract_leading_comment(lines, start_line)
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
                summary=summary,
                call_list=extract_call_list(body),
            )
        )
    return methods


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
    bare_name = name.split("::")[-1].strip()
    if bare_name in CONTROL_KEYWORDS or bare_name in MACRO_LIKE_NAMES:
        return False
    if bare_name.isupper() and "::" not in name:
        return False
    return True


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


def extract_leading_comment(lines: List[str], start_line: int) -> str:
    index = start_line - 2
    skipped_blank = 0
    while index >= 0 and not lines[index].strip() and skipped_blank < 2:
        skipped_blank += 1
        index -= 1
    if index < 0:
        return ""
    line = lines[index].rstrip()
    if line.strip().endswith("*/"):
        block: list[str] = []
        while index >= 0:
            block.insert(0, lines[index])
            if "/*" in lines[index]:
                break
            index -= 1
        cleaned = clean_comment("\n".join(block))
        return cleaned[:1200] if is_meaningful_comment(cleaned) else ""
    if line.lstrip().startswith("//"):
        block = []
        while index >= 0 and lines[index].lstrip().startswith("//"):
            block.insert(0, lines[index])
            index -= 1
        cleaned = clean_comment("\n".join(block))
        return cleaned[:1200] if is_meaningful_comment(cleaned) else ""
    return ""


def extract_call_list(body: str, *, limit: int = 40) -> List[str]:
    calls: List[str] = []
    seen: set[str] = set()
    for match in CALL_RE.finditer(strip_comments_keep_lines(body)):
        name = match.group(1)
        if name in CONTROL_KEYWORDS or name in seen:
            continue
        seen.add(name)
        calls.append(name)
        if len(calls) >= limit:
            break
    return calls


def method_id(*, file_id: str, qualified_name: str, start_line: int, end_line: int) -> str:
    return f"{file_id}::{qualified_name}@{start_line}-{end_line}"


def method_doc(method: MethodCandidate) -> dict[str, Any]:
    return {
        "id": method.id,
        "doc_type": "method",
        "file": method.file,
        "item_type": method.item_type,
        "qualified_name": method.qualified_name,
        "start_line": method.start_line,
        "end_line": method.end_line,
        "summary": method.summary,
        "source": "leading_explanatory_comment",
    }


def method_doc_path(root: Path, method: MethodCandidate) -> Path:
    file_path = Path(method.file)
    if file_path.suffix:
        file_dir = root / file_path.with_suffix("")
    else:
        file_dir = root / file_path
    stem = safe_filename(f"{method.item_type}__{method.qualified_name}", max_chars=120)
    return file_dir / f"{stem}__{sha1_text(method.id)[:12]}.json"


def normalize_signature(signature: str) -> str:
    return " ".join(signature.replace("\t", " ").split())


def strip_block_comments_keep_lines(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    return re.sub(r"/\*.*?\*/", repl, text, flags=re.S)


def strip_comments_keep_lines(text: str) -> str:
    return "\n".join(strip_line_comment(line) for line in strip_block_comments_keep_lines(text).splitlines())


def strip_line_comment(line: str) -> str:
    return re.sub(r"//.*", "", line)


def is_inside_existing_range(line_number: int, ranges: Iterable[tuple[int, int]]) -> bool:
    return any(start < line_number <= end for start, end in ranges)


def safe_filename(value: str, *, max_chars: int) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value or "method"))
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return (cleaned or "method")[:max_chars]


def sha1_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="replace")).hexdigest()
