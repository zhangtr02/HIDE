from __future__ import annotations

from typing import Iterable, List

from .methods import MethodCandidate


FILE_LEVEL_PROMPT = """
Please look through the following GitHub problem description and Repository structure and provide a list of files that one would need to edit to fix the problem.

### GitHub Problem Description ###
{problem_statement}

###

### Repository Structure ###
{structure}

###

Please only provide the full path and return at most {top_n} files.
The returned files should be separated by new lines ordered by most to least important and wrapped with ```
For example:
```
gcc/cp/pt.cc
gcc/tree-vrp.cc
```
"""


RELATED_LEVEL_PROMPT = """
Please look through the following GitHub Problem Description and the Skeleton of Relevant Files.
Identify all locations that need inspection or editing to fix the problem, including directly related areas as well as any potentially related functions and methods.
For each location you provide, give the name of a function or method.

### GitHub Problem Description ###
{problem_statement}

### Skeleton of Relevant Files ###
{file_contents}

###

Please provide the complete set of locations as function or method names.
### Examples:
```
gcc/cp/pt.cc
function: instantiate_decl
function: find_parameter_packs_r

gcc/tree-vrp.cc
function: execute_vrp
```

Return just the locations wrapped with ```.
"""


LINE_LEVEL_PROMPT = """
Please review the following GitHub problem description and relevant files, and provide a set of locations that need to be edited to fix the issue.
The locations can be specified as function or method names, or exact line numbers that require modification.

### GitHub Problem Description ###
{problem_statement}

###
{file_contents}

###

Please provide the function or method name, or the exact line numbers that need to be edited.
The possible location outputs should be either "function", "method", or "line".

### Examples:
```
gcc/cp/pt.cc
line: 10
function: instantiate_decl

gcc/tree-vrp.cc
function: execute_vrp
line: 24
```

Return just the location(s) wrapped with ```.
"""


def build_file_prompt(problem_statement: str, structure: str, *, top_n: int) -> str:
    return FILE_LEVEL_PROMPT.format(problem_statement=problem_statement, structure=structure, top_n=top_n).strip()


def build_related_prompt(problem_statement: str, methods: Iterable[MethodCandidate]) -> str:
    return RELATED_LEVEL_PROMPT.format(
        problem_statement=problem_statement,
        file_contents=build_method_skeleton(methods),
    ).strip()


def build_line_prompt(problem_statement: str, methods: Iterable[MethodCandidate], *, max_chars: int) -> str:
    return LINE_LEVEL_PROMPT.format(
        problem_statement=problem_statement,
        file_contents=build_line_context(methods, max_chars=max_chars),
    ).strip()


def build_method_skeleton(methods: Iterable[MethodCandidate]) -> str:
    by_file: dict[str, List[MethodCandidate]] = {}
    for method in methods:
        by_file.setdefault(method.file, []).append(method)
    chunks: List[str] = []
    for file_id, file_methods in by_file.items():
        lines = [f"### File: {file_id} ###", "```c++"]
        for method in file_methods:
            lines.append(
                f"{method.item_type}: {method.qualified_name} "
                f"@ lines {method.start_line}-{method.end_line}"
            )
            lines.append(f"signature: {method.signature}")
        lines.append("```")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


def build_line_context(methods: Iterable[MethodCandidate], *, max_chars: int) -> str:
    chunks: List[str] = []
    remaining = max_chars
    for method in methods:
        if remaining <= 0:
            break
        body = line_wrap_body(method)
        chunk = (
            f"### File: {method.file} ###\n"
            f"### Location: {method.item_type}: {method.qualified_name} "
            f"@ lines {method.start_line}-{method.end_line} ###\n"
            f"```c++\n{body}\n```"
        )
        if len(chunk) > remaining:
            chunk = chunk[: max(0, remaining - 40)] + "\n/* ... truncated ... */"
        chunks.append(chunk)
        remaining -= len(chunk)
    return "\n\n".join(chunks)


def line_wrap_body(method: MethodCandidate) -> str:
    lines = method.body.splitlines()
    return "\n".join(f"{method.start_line + index}: {line}" for index, line in enumerate(lines))
