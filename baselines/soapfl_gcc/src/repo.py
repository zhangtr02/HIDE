from __future__ import annotations

import re
from pathlib import Path
from typing import List

from .io_utils import unique_keep_order


SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".inc",
    ".def",
    ".opt",
    ".md",
    ".pd",
}
CODE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".inc"}
SKIP_DIRS = {".git", "__pycache__"}
SKIP_PREFIXES = ("gcc/testsuite/",)
LICENSE_RE = re.compile(
    r"copyright|free software foundation|gnu general public license|this file is part of gcc|gcc is free software",
    re.I,
)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT_RE = re.compile(r"(?m)//.*$")
SYMBOL_RE = re.compile(
    r"^\s*(?:static\s+|extern\s+|inline\s+|virtual\s+|constexpr\s+|template\s*<[^>]+>\s*)*"
    r"(?:[A-Za-z_][A-Za-z0-9_:<>*&\s]+\s+)?"
    r"(?P<name>(?:[A-Za-z_][A-Za-z0-9_]*::)*~?[A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.M,
)


def list_gcc_source_files(gcc_repo_dir: Path) -> List[str]:
    gcc_dir = gcc_repo_dir / "gcc"
    if not gcc_dir.is_dir():
        return []
    files: List[str] = []
    for path in sorted(gcc_dir.rglob("*")):
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        rel = path.relative_to(gcc_repo_dir).as_posix()
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if rel.startswith(SKIP_PREFIXES):
            continue
        files.append(rel)
    return unique_keep_order(files)


def read_file_summary(gcc_repo_dir: Path, file_id: str, *, max_chars: int) -> str:
    path = gcc_repo_dir / file_id
    if not path.is_file():
        return f"GCC source file {file_id}."
    text = path.read_text(encoding="utf-8", errors="replace")
    comments = extract_meaningful_comments(text, max_comments=3)
    symbols = unique_keep_order(match.group("name") for match in SYMBOL_RE.finditer(strip_comments_keep_lines(text)))
    parts = [f"GCC source file {file_id}."]
    if comments:
        parts.append("Existing comments: " + " ".join(comments))
    if symbols:
        parts.append("Visible functions/methods include: " + ", ".join(symbols[:12]) + ".")
    return " ".join(parts)[:max_chars].strip()


def extract_meaningful_comments(text: str, *, max_comments: int) -> List[str]:
    body = remove_initial_license_header(text)
    comments: List[str] = []
    for raw in [*BLOCK_COMMENT_RE.findall(body), *LINE_COMMENT_RE.findall(body)]:
        cleaned = clean_comment(raw)
        if is_meaningful_comment(cleaned):
            comments.append(cleaned)
        if len(comments) >= max_comments:
            break
    return comments


def remove_initial_license_header(text: str) -> str:
    stripped = text.lstrip()
    match = re.match(r"/\*.*?\*/", stripped, re.S)
    if match and LICENSE_RE.search(match.group(0)):
        stripped = stripped[match.end() :]
    lines = stripped.splitlines(True)
    index = 0
    header: List[str] = []
    while index < len(lines) and (not lines[index].strip() or lines[index].lstrip().startswith("//")):
        header.append(lines[index])
        index += 1
    if header and LICENSE_RE.search("".join(header)):
        stripped = "".join(lines[index:])
    return stripped


def clean_comment(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("/*"):
        text = text[2:]
    if text.endswith("*/"):
        text = text[:-2]
    lines = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:\*+|//)\s?", "", line).rstrip()
        if line:
            lines.append(line)
    return " ".join(" ".join(lines).split())


def is_meaningful_comment(text: str) -> bool:
    if len(text.strip()) < 20:
        return False
    if LICENSE_RE.search(text):
        return False
    return True


def strip_comments_keep_lines(text: str) -> str:
    def block_repl(match: re.Match[str]) -> str:
        return "\n" * match.group(0).count("\n")

    text = re.sub(r"/\*.*?\*/", block_repl, text, flags=re.S)
    return re.sub(r"//.*", "", text)


def file_doc_path(root: Path, file_id: str) -> Path:
    path = Path(file_id)
    if path.suffix:
        return root / path.with_suffix(".json")
    return root / path / "index.json"


def build_file_doc(gcc_repo_dir: Path, file_id: str, *, max_chars: int) -> dict[str, str]:
    return {
        "doc_type": "file",
        "path": file_id,
        "summary": read_file_summary(gcc_repo_dir, file_id, max_chars=max_chars),
    }


