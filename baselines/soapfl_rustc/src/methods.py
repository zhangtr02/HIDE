from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List


FN_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?"
    r"(?:(?:default|async|const|unsafe)\s+)*"
    r"(?:extern\s+\"[^\"]+\"\s+)?"
    r"fn\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\b"
)
ITEM_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?"
    r"(?:(?:unsafe|auto)\s+)*"
    r"(?P<kind>impl|trait|struct|enum|mod)\b(?P<name>[^{;]*)"
)
CALL_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*(?:::<[^>\n]+>)?\s*\(")
KEYWORD_CALLS = {
    "if",
    "for",
    "while",
    "loop",
    "match",
    "return",
    "Some",
    "None",
    "Ok",
    "Err",
    "Self",
}


@dataclass(frozen=True)
class RustItem:
    item_type: str
    item_name: str
    signature: str
    start_line: int
    end_line: int
    parent_signature: str = ""

    @property
    def qualified_name(self) -> str:
        if self.parent_signature and self.item_type in {"method", "trait_method"}:
            return f"{self.parent_signature}::{self.item_name}"
        return self.item_name


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
    call_list: List[str]

    def to_prompt_dict(self, *, include_body: bool = True, body_chars: int | None = None, summary: str = "") -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "file": self.file,
            "item_type": self.item_type,
            "item_name": self.item_name,
            "qualified_name": self.qualified_name,
            "signature": self.signature,
            "parent_signature": self.parent_signature,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "calls": self.call_list,
        }
        if summary:
            data["summary"] = summary
        if include_body:
            data["body"] = self.body if body_chars is None else self.body[:body_chars]
        return data

    def to_report_dict(self, *, reason: str = "", score: int | None = None, summary: str = "", source: str = "") -> dict[str, Any]:
        data = self.to_prompt_dict(include_body=False, summary=summary)
        if reason:
            data["reason"] = reason
        if score is not None:
            data["score"] = score
        if source:
            data["source"] = source
        return data


class MethodStore:
    def __init__(self, *, rust_repo_dir: Path) -> None:
        self.rust_repo_dir = rust_repo_dir

    def load_methods_for_file(self, file_id: str) -> list[MethodCandidate]:
        path = self.rust_repo_dir / file_id
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
        return extract_method_candidates(file_id=file_id, text=text)


def extract_method_candidates(*, file_id: str, text: str) -> list[MethodCandidate]:
    lines = text.splitlines()
    out: list[MethodCandidate] = []
    for item in parse_rust_items(lines):
        if item.item_type not in {"function", "method", "trait_method"}:
            continue
        body = "\n".join(lines[item.start_line - 1 : item.end_line])
        call_list = extract_call_list(body)
        out.append(
            MethodCandidate(
                id=method_id(file_id=file_id, item=item),
                file=file_id,
                item_type=item.item_type,
                item_name=item.item_name,
                qualified_name=item.qualified_name,
                signature=item.signature,
                parent_signature=item.parent_signature,
                start_line=item.start_line,
                end_line=item.end_line,
                body=body,
                call_list=call_list,
            )
        )
    return out


def method_id(*, file_id: str, item: RustItem) -> str:
    return f"{file_id}::{item.qualified_name}@{item.start_line}-{item.end_line}"


def parse_rust_items(lines: List[str]) -> List[RustItem]:
    raw_items: list[RustItem] = []
    for index, line in enumerate(lines, start=1):
        fn_match = FN_RE.match(line)
        if fn_match:
            raw_items.append(build_item(lines, start_line=index, item_type="function", item_name=fn_match.group("name")))
            continue
        item_match = ITEM_RE.match(line)
        if item_match:
            raw_items.append(
                build_item(
                    lines,
                    start_line=index,
                    item_type=item_match.group("kind"),
                    item_name=clean_item_name(item_match.group("kind"), item_match.group("name")),
                )
            )

    items: list[RustItem] = []
    for item in raw_items:
        if item.item_type != "function":
            items.append(item)
            continue
        parent = enclosing_parent(raw_items, item)
        item_type = (
            "method"
            if parent and parent.item_type == "impl"
            else "trait_method"
            if parent and parent.item_type == "trait"
            else "function"
        )
        items.append(
            RustItem(
                item_type=item_type,
                item_name=item.item_name,
                signature=item.signature,
                start_line=item.start_line,
                end_line=item.end_line,
                parent_signature=clean_parent_signature(parent.signature) if parent else "",
            )
        )
    return items


def build_item(lines: List[str], *, start_line: int, item_type: str, item_name: str) -> RustItem:
    signature, signature_end = collect_signature(lines, start_line)
    return RustItem(
        item_type=item_type,
        item_name=item_name,
        signature=signature,
        start_line=start_line,
        end_line=find_item_end_line(lines, start_line, signature_end),
    )


def collect_signature(lines: List[str], start_line: int, *, max_lines: int = 25) -> tuple[str, int]:
    parts: list[str] = []
    end_line = start_line
    for line_number in range(start_line, min(len(lines), start_line + max_lines - 1) + 1):
        text = lines[line_number - 1].strip()
        parts.append(text)
        end_line = line_number
        if "{" in text or text.endswith(";"):
            break
    return " ".join(part for part in parts if part)[:1000], end_line


def find_item_end_line(lines: List[str], start_line: int, signature_end: int) -> int:
    depth = 0
    found_open = False
    for line_number in range(start_line, len(lines) + 1):
        line = strip_strings(lines[line_number - 1])
        for char in line:
            if char == "{":
                found_open = True
                depth += 1
            elif char == "}":
                depth -= 1
                if found_open and depth <= 0:
                    return line_number
        if not found_open and line_number >= signature_end:
            return signature_end
    return len(lines)


def strip_strings(line: str) -> str:
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', line)


def clean_item_name(kind: str, name: str) -> str:
    text = " ".join(str(name or "").strip().split())
    if kind == "impl":
        return truncate(text or "impl", 220)
    match = re.search(r"[A-Za-z_][A-Za-z0-9_]*", text)
    return truncate(match.group(0) if match else kind, 220)


def clean_parent_signature(signature: str) -> str:
    return signature.rstrip().rstrip("{").strip()


def enclosing_parent(items: list[RustItem], item: RustItem) -> RustItem | None:
    parents = [
        parent
        for parent in items
        if parent.item_type in {"impl", "trait"}
        and parent.start_line < item.start_line
        and item.end_line <= parent.end_line
    ]
    if not parents:
        return None
    return max(parents, key=lambda parent: parent.start_line)


def extract_call_list(body: str, *, limit: int = 40) -> List[str]:
    calls: List[str] = []
    seen: set[str] = set()
    for match in CALL_RE.finditer(strip_comments(body)):
        name = match.group(1)
        if name in KEYWORD_CALLS or name in seen:
            continue
        seen.add(name)
        calls.append(name)
        if len(calls) >= limit:
            break
    return calls


def strip_comments(text: str) -> str:
    text = re.sub(r"//.*", "", text)
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."

