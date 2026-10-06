from __future__ import annotations

import re
from typing import Dict, Iterable, List

from .io_utils import unique_keep_order
from .methods import MethodCandidate, method_at_line


def extract_code_blocks(text: str) -> List[str]:
    blocks = re.findall(r"```(?:[A-Za-z0-9_+-]+)?\s*(.*?)```", text or "", flags=re.DOTALL)
    return [block.strip() for block in blocks if block.strip()] or [str(text or "").strip()]


def parse_file_list(raw_output: str, candidate_files: Iterable[str], *, top_n: int) -> List[str]:
    candidates = list(candidate_files)
    found: List[str] = []
    for block in extract_code_blocks(raw_output):
        for line in block.splitlines():
            file_id = extract_candidate_file(line, candidates)
            if file_id:
                found.append(file_id)
    return unique_keep_order(found)[:top_n]


def parse_location_blocks(raw_output: str, candidate_files: Iterable[str]) -> Dict[str, List[str]]:
    candidates = list(candidate_files)
    locations: Dict[str, List[str]] = {}
    current_file = ""
    for block in extract_code_blocks(raw_output):
        for raw_line in block.splitlines():
            line = normalize_line(raw_line)
            if not line:
                continue
            file_id = extract_candidate_file(line, candidates)
            if file_id:
                current_file = file_id
                locations.setdefault(current_file, [])
                continue
            if current_file and looks_like_location(line):
                locations.setdefault(current_file, []).append(line)
    return {file_id: unique_keep_order(items) for file_id, items in locations.items()}


def match_methods_from_locations(
    locations: Dict[str, List[str]],
    methods: Iterable[MethodCandidate],
) -> List[MethodCandidate]:
    by_file: Dict[str, List[MethodCandidate]] = {}
    for method in methods:
        by_file.setdefault(method.file, []).append(method)

    matched: List[MethodCandidate] = []
    for file_id, locs in locations.items():
        candidates = by_file.get(file_id, [])
        for loc in locs:
            line_numbers = parse_line_numbers(loc)
            if line_numbers:
                for line_number in line_numbers:
                    method = method_at_line(candidates, file_id, line_number)
                    if method is not None:
                        matched.append(method)
                continue
            method = match_method_by_name(candidates, loc)
            if method is not None:
                matched.append(method)
    return unique_methods(matched)


def parse_line_locations(raw_output: str, candidate_files: Iterable[str]) -> Dict[str, List[int]]:
    raw_locations = parse_location_blocks(raw_output, candidate_files)
    out: Dict[str, List[int]] = {}
    for file_id, locs in raw_locations.items():
        for loc in locs:
            for line_number in parse_line_numbers(loc):
                out.setdefault(file_id, []).append(line_number)
    return {file_id: sorted(set(lines)) for file_id, lines in out.items()}


def map_lines_to_methods(
    line_locations: Dict[str, List[int]],
    methods: Iterable[MethodCandidate],
) -> List[MethodCandidate]:
    all_methods = list(methods)
    matched: List[MethodCandidate] = []
    for file_id, lines in line_locations.items():
        for line_number in lines:
            method = method_at_line(all_methods, file_id, line_number)
            if method is not None:
                matched.append(method)
    return unique_methods(matched)


def match_method_by_name(candidates: List[MethodCandidate], location: str) -> MethodCandidate | None:
    name = normalize_location_name(location)
    if not name:
        return None

    plain_name = strip_generics(name)
    for method in candidates:
        if name == normalize_name(method.qualified_name):
            return method
    for method in candidates:
        if name == normalize_name(method.item_name):
            return method
    for method in candidates:
        qualified = normalize_name(method.qualified_name)
        plain_qualified = strip_generics(qualified)
        if qualified.endswith(f"::{name}") or plain_qualified.endswith(plain_name):
            return method

    terminal_name = name.rsplit("::", 1)[-1]
    for method in candidates:
        if terminal_name == normalize_name(method.item_name):
            return method
    return None


def parse_line_numbers(location: str) -> List[int]:
    line_match = re.search(r"\blines?\s*:\s*([0-9,\s-]+)", location, flags=re.IGNORECASE)
    if line_match:
        return expand_line_numbers(line_match.group(1))
    exact_match = re.search(r"^\s*(\d+)\s*$", normalize_line(location))
    if exact_match:
        return [int(exact_match.group(1))]
    return []


def expand_line_numbers(text: str) -> List[int]:
    out: List[int] = []
    for part in re.split(r"\s*,\s*", text.strip()):
        if not part:
            continue
        range_match = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if start <= end:
                out.extend(range(start, min(end, start + 20) + 1))
            continue
        if part.isdigit():
            out.append(int(part))
    return sorted(set(out))


def extract_candidate_file(line: str, candidate_files: Iterable[str]) -> str:
    normalized = normalize_line(line)
    candidate_set = set(candidate_files)
    if normalized in candidate_set:
        return normalized
    for candidate in sorted(candidate_set, key=len, reverse=True):
        if candidate in normalized:
            return candidate
    return ""


def strip_generics(value: str) -> str:
    previous = ""
    text = value
    while previous != text:
        previous = text
        text = re.sub(r"<[^<>]*>", "", text)
    return normalize_name(text)


def normalize_location_name(location: str) -> str:
    text = re.sub(
        r"^(function|method)\s*:\s*",
        "",
        normalize_line(location),
        flags=re.IGNORECASE,
    )
    text = text.strip("` ")
    return normalize_name(text)


def normalize_name(value: str) -> str:
    return " ".join(str(value or "").replace(":: ", "::").strip().split())


def looks_like_location(line: str) -> bool:
    return bool(re.match(r"^(function|method|lines?)\s*:", line, flags=re.IGNORECASE))


def normalize_line(line: str) -> str:
    text = str(line or "").strip().strip("-*` ")
    text = re.sub(r"^\s*\d+\s*[.)]\s*", "", text)
    return text.strip()


def unique_methods(methods: Iterable[MethodCandidate]) -> List[MethodCandidate]:
    out: List[MethodCandidate] = []
    seen: set[str] = set()
    for method in methods:
        if method.id in seen:
            continue
        seen.add(method.id)
        out.append(method)
    return out
