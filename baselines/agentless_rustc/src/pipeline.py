from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from .config import RuntimeConfig
from .context import build_problem_statement, load_issue
from .git_utils import checkout_ref
from .io_utils import load_json_if_exists, unique_keep_order, write_json
from .llm import LLMClient
from .methods import MethodCandidate, MethodStore
from .parsing import (
    map_lines_to_methods,
    match_methods_from_locations,
    parse_file_list,
    parse_line_locations,
    parse_location_blocks,
)
from .prompts import build_file_prompt, build_irrelevant_filter_prompt, build_line_prompt, build_related_prompt
from .retrieval import combine_file_rankings, filter_irrelevant_files, parse_irrelevant_paths
from .repo import build_compiler_structure, list_compiler_rs_files


@dataclass(frozen=True)
class PipelineOptions:
    checkout: bool = True


class AgentlessRustcPipeline:
    def __init__(self, *, cfg: RuntimeConfig, client: LLMClient, options: PipelineOptions) -> None:
        self.cfg = cfg
        self.client = client
        self.options = options

    def run_bug(self, *, bug_id: str, toolchain: str) -> Dict[str, Any]:
        total_start = time.time()
        stage_times: Dict[str, float] = {}
        usage_start_index = len(self.client.usage_tracker.calls)

        checkout_commit = ""
        if self.options.checkout and toolchain:
            checkout_commit = timed_value(stage_times, "checkout", lambda: checkout_ref(self.cfg.paths.rust_repo_dir, toolchain))

        issue = timed_value(stage_times, "load_issue", lambda: load_issue(self.cfg.paths.issue_dir, bug_id))
        problem_statement = build_problem_statement(issue)
        candidate_files = timed_value(stage_times, "enumerate_files", lambda: list_compiler_rs_files(self.cfg.paths.rust_repo_dir))
        structure = timed_value(stage_times, "build_repo_structure", lambda: build_compiler_structure(candidate_files))

        file_artifact = timed_value(
            stage_times,
            "file_level_llm",
            lambda: self.run_file_level(problem_statement=problem_statement, structure=structure, candidate_files=candidate_files),
        )
        irrelevant_artifact = timed_value(
            stage_times,
            "irrelevant_filter_llm",
            lambda: self.run_irrelevant_filter(
                problem_statement=problem_statement,
                structure=structure,
                candidate_files=candidate_files,
            ),
        )
        retrieval_artifact = timed_value(
            stage_times,
            "embedding_retrieval",
            lambda: self.run_embedding_retrieval(
                bug_id=bug_id,
            ),
        )
        found_files = combine_file_rankings(
            file_artifact["llm_found_files"],
            retrieval_artifact["found_files"],
            top_n=self.cfg.agentless.file_top_n,
            retrieval_top_n=self.cfg.agentless.retrieval_top_n,
        )
        related_artifact: Dict[str, Any] = {"enabled": False, "matched_methods": []}
        line_artifact: Dict[str, Any] = {"enabled": False, "mapped_methods": []}
        related_methods: List[MethodCandidate] = []
        line_methods: List[MethodCandidate] = []
        final_methods: List[MethodCandidate] = []
        if self.cfg.agentless.run_method_level:
            related_input_files = found_files[: self.cfg.agentless.related_top_n]
            method_store = MethodStore(rust_repo_dir=self.cfg.paths.rust_repo_dir)
            method_candidates = timed_value(
                stage_times,
                "load_method_candidates",
                lambda: method_store.load_methods_for_files(related_input_files),
            )
            related_artifact = timed_value(
                stage_times,
                "related_level_llm",
                lambda: self.run_related_level(
                    problem_statement=problem_statement,
                    methods=method_candidates,
                    input_files=related_input_files,
                ),
            )
            related_artifact["enabled"] = True
            related_methods = related_artifact["matched_methods"]

            line_input_methods = related_methods or method_candidates
            line_artifact = timed_value(
                stage_times,
                "line_level_llm",
                lambda: self.run_line_level(
                    problem_statement=problem_statement,
                    methods=line_input_methods,
                    input_files=related_input_files,
                ),
            )
            line_artifact["enabled"] = True
            line_methods = line_artifact["mapped_methods"]
            final_methods = unique_methods([*line_methods, *related_methods, *method_candidates])[
                : self.cfg.agentless.method_top_k
            ]
        report = {
            "bug_id": bug_id,
            "method_level_enabled": self.cfg.agentless.run_method_level,
            "checkout": {
                "requested": toolchain,
                "resolved": checkout_commit,
            },
            "final_top10": found_files[: self.cfg.agentless.file_top_n],
            "final_method_top10": [method.id for method in final_methods],
            "final_method_items": [method.to_report_dict(source="final") for method in final_methods],
            "file_level": {
                **file_artifact,
                "irrelevant_filter": irrelevant_artifact,
                "retrieval": retrieval_artifact,
                "found_files": found_files,
            },
            "related_level": {
                **related_artifact,
                "matched_methods": [method.to_report_dict(source="related_level") for method in related_methods],
            },
            "line_level": {
                **line_artifact,
                "mapped_methods": [method.to_report_dict(source="line_level") for method in line_methods],
            },
            "efficiency": {
                "total_wall_time_sec": round(time.time() - total_start, 3),
                "stage_times_sec": {key: round(value, 3) for key, value in stage_times.items()},
                "llm_usage": self.client.usage_tracker.to_dict(start_index=usage_start_index),
            },
        }
        write_json(self.cfg.paths.report_dir / f"{bug_id}.json", report)
        return report

    def run_file_level(self, *, problem_statement: str, structure: str, candidate_files: List[str]) -> Dict[str, Any]:
        prompt = build_file_prompt(problem_statement, structure, top_n=self.cfg.agentless.file_top_n)
        raw_output = self.client.chat_text(stage="file_level", prompt=prompt, max_tokens=self.cfg.llm.max_tokens)
        found_files = parse_file_list(raw_output, candidate_files, top_n=self.cfg.agentless.file_top_n)
        return {
            "candidate_file_count": len(candidate_files),
            "llm_found_files": found_files,
            "raw_output": raw_output,
        }

    def run_irrelevant_filter(self, *, problem_statement: str, structure: str, candidate_files: List[str]) -> Dict[str, Any]:
        if not self.cfg.agentless.enable_irrelevant_filter:
            return {
                "enabled": False,
                "raw_output": "",
                "irrelevant_paths": [],
                "kept_files": candidate_files,
                "filtered_files": [],
            }
        prompt = build_irrelevant_filter_prompt(problem_statement, structure)
        raw_output = self.client.chat_text(stage="irrelevant_filter", prompt=prompt, max_tokens=self.cfg.llm.max_tokens)
        irrelevant_paths = parse_irrelevant_paths(raw_output, candidate_files)
        kept_files, filtered_files = filter_irrelevant_files(candidate_files, irrelevant_paths)
        return {
            "enabled": True,
            "raw_output": raw_output,
            "irrelevant_paths": irrelevant_paths,
            "kept_files": kept_files,
            "filtered_files": filtered_files,
            "kept_file_count": len(kept_files),
            "filtered_file_count": len(filtered_files),
        }

    def run_embedding_retrieval(self, *, bug_id: str) -> Dict[str, Any]:
        path = self.cfg.paths.embedding_dir / f"{bug_id}.json"
        artifact = load_json_if_exists(path)
        if not isinstance(artifact, dict):
            raise RuntimeError(
                f"Missing frozen embedding retrieval for bug {bug_id}: {path}. "
                "Run `python3 -m src.extract_embedding` before localization."
            )
        if not isinstance(artifact.get("found_files"), list) or not isinstance(artifact.get("node_info"), list):
            raise RuntimeError(f"Invalid frozen embedding retrieval for bug {bug_id}: {path}")
        return {
            **artifact,
            "reused": True,
            "source_path": str(path),
        }

    def run_related_level(
        self,
        *,
        problem_statement: str,
        methods: List[MethodCandidate],
        input_files: List[str],
    ) -> Dict[str, Any]:
        if not methods:
            return {
                "input_files": input_files,
                "method_candidate_count": 0,
                "found_locations": {},
                "raw_output": "",
                "matched_methods": [],
            }
        prompt = build_related_prompt(problem_statement, methods)
        raw_output = self.client.chat_text(stage="related_level", prompt=prompt, max_tokens=self.cfg.llm.max_tokens)
        found_locations = parse_location_blocks(raw_output, input_files)
        matched_methods = match_methods_from_locations(found_locations, methods)
        return {
            "input_files": input_files,
            "method_candidate_count": len(methods),
            "found_locations": found_locations,
            "raw_output": raw_output,
            "matched_methods": matched_methods,
        }

    def run_line_level(
        self,
        *,
        problem_statement: str,
        methods: List[MethodCandidate],
        input_files: List[str],
    ) -> Dict[str, Any]:
        if not methods:
            return {
                "input_files": input_files,
                "input_method_count": 0,
                "found_lines": {},
                "raw_output": "",
                "mapped_methods": [],
            }
        prompt = build_line_prompt(problem_statement, methods, max_chars=self.cfg.agentless.max_line_context_chars)
        raw_output = self.client.chat_text(stage="line_level", prompt=prompt, max_tokens=self.cfg.llm.max_tokens)
        found_lines = parse_line_locations(raw_output, input_files)
        mapped_methods = map_lines_to_methods(found_lines, methods)
        if not mapped_methods:
            found_locations = parse_location_blocks(raw_output, input_files)
            mapped_methods = match_methods_from_locations(found_locations, methods)
        return {
            "input_files": input_files,
            "input_method_count": len(methods),
            "found_lines": found_lines,
            "raw_output": raw_output,
            "mapped_methods": mapped_methods,
        }


def unique_methods(methods: List[MethodCandidate]) -> List[MethodCandidate]:
    out: List[MethodCandidate] = []
    seen: set[str] = set()
    for method in methods:
        if method.id in seen:
            continue
        seen.add(method.id)
        out.append(method)
    return out


def timed_value(stage_times: Dict[str, float], key: str, fn: Callable[[], Any]) -> Any:
    start = time.time()
    value = fn()
    stage_times[key] = time.time() - start
    return value
