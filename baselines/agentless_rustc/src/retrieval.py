from __future__ import annotations

from collections import Counter
from typing import Iterable, List

from .parsing import extract_code_blocks, normalize_line


def parse_irrelevant_paths(raw_output: str, candidate_files: Iterable[str]) -> List[str]:
    candidate_set = set(candidate_files)
    paths: List[str] = []
    for block in extract_code_blocks(raw_output):
        for raw_line in block.splitlines():
            line = normalize_irrelevant_path(raw_line)
            if not line:
                continue
            if line.endswith("/") or line in candidate_set:
                paths.append(line)
                continue
            for candidate in candidate_set:
                if candidate in line:
                    paths.append(candidate)
                    break
    return unique_keep_order(paths)


def normalize_irrelevant_path(line: str) -> str:
    text = normalize_line(line)
    text = text.split("#", 1)[0].strip()
    text = text.rstrip(":")
    if not text.startswith("compiler/"):
        index = text.find("compiler/")
        text = text[index:] if index >= 0 else text
    return text.strip()


def filter_irrelevant_files(candidate_files: Iterable[str], irrelevant_paths: Iterable[str]) -> tuple[List[str], List[str]]:
    kept: List[str] = []
    filtered: List[str] = []
    paths = [path for path in irrelevant_paths if path]
    for file_id in candidate_files:
        if is_irrelevant_file(file_id, paths):
            filtered.append(file_id)
        else:
            kept.append(file_id)
    return kept, filtered


def is_irrelevant_file(file_id: str, irrelevant_paths: Iterable[str]) -> bool:
    for path in irrelevant_paths:
        if path.endswith("/") and file_id.startswith(path):
            return True
        if file_id == path:
            return True
    return False


def combine_file_rankings(model_files: Iterable[str], retrieval_files: Iterable[str], *, top_n: int, retrieval_top_n: int) -> List[str]:
    counter: Counter[str] = Counter()
    for file_id in list(model_files)[:top_n]:
        counter[file_id] += 1
    for file_id in list(retrieval_files)[:retrieval_top_n]:
        counter[file_id] += 1
    return [file_id for file_id, _count in counter.most_common()][:top_n]


def unique_keep_order(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
