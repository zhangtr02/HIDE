from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from causal_agent import CausalAgent
from config import RuntimeConfig
from context import (
    build_bug_context,
    checkout_gcc_repo,
    ensure_dir,
    get_repo_head,
)
from convert_results import convert_results, serialize_cig, unique_keep_order
from graph import DependencyExpansionOptions, load_or_build_gcc_graph
from llm import GraphLocatorLLM
from search_agent import SearchAgent
from utils.string_processing import node_to_json


@dataclass(frozen=True)
class PipelineOptions:
    checkout: bool = True


class GraphLocatorGccPipeline:
    def __init__(self, cfg: RuntimeConfig, options: PipelineOptions) -> None:
        self.cfg = cfg
        self.options = options

    def run(self, bug_id: str) -> Dict[str, Any]:
        total_start = time.time()
        stage_times: Dict[str, float] = {}
        report_path = self.cfg.report_dir / f"{bug_id}.json"
        ctx = timed_value(stage_times, "load_bug_context", lambda: build_bug_context(self.cfg, bug_id))
        if self.options.checkout:
            checkout_commit = timed_value(stage_times, "checkout", lambda: checkout_gcc_repo(self.cfg.paths.gcc_repo_dir, ctx.base_commit))
        else:
            checkout_commit = timed_value(stage_times, "get_head", lambda: get_repo_head(self.cfg.paths.gcc_repo_dir))

        graph_result = timed_value(
            stage_times,
            "load_or_build_graph",
            lambda: load_or_build_gcc_graph(
                self.cfg,
                bug_id=bug_id,
                checkout_commit=checkout_commit,
            ),
        )
        initial_graph_node_count = graph_result.graph.graph.number_of_nodes()
        initial_graph_edge_count = graph_result.graph.graph.number_of_edges()
        llm = GraphLocatorLLM(self.cfg.llm)
        usage_start_index = len(llm.usage_tracker.calls)
        dependency_options = DependencyExpansionOptions(
            max_files=self.cfg.graphlocator.dependency_max_files,
            max_nodes_per_file=self.cfg.graphlocator.dependency_max_nodes_per_file,
            include_file_dependencies=self.cfg.graphlocator.include_file_dependencies,
            include_class_dependencies=self.cfg.graphlocator.include_class_dependencies,
            include_method_dependencies=self.cfg.graphlocator.include_method_dependencies,
        )

        search_agent = SearchAgent(
            repo_graph=graph_result.graph,
            llm=llm,
            max_search_turn=self.cfg.graphlocator.max_search_turn,
            top_k=self.cfg.graphlocator.search_topk,
            max_code_elements=self.cfg.graphlocator.max_causal_code_elements,
            node_content_chars=self.cfg.graphlocator.max_causal_node_content_chars,
            expand_dependencies=self.cfg.graphlocator.expand_dependencies_in_search,
            dependency_options=dependency_options,
        )
        seed_locations = timed_value(
            stage_times,
            "symptom_vertices_locating",
            lambda: search_agent.get_seed_location(ctx.problem_statement),
        )

        causal_agent = CausalAgent(
            llm=llm,
            repo_graph=search_agent.repo_graph,
            max_turn=self.cfg.graphlocator.max_causal_turn,
            max_code_elements=self.cfg.graphlocator.max_causal_code_elements,
            node_content_chars=self.cfg.graphlocator.max_causal_node_content_chars,
            existing_node_content_chars=self.cfg.graphlocator.max_existing_node_content_chars,
            max_prompt_chars=self.cfg.graphlocator.max_causal_prompt_chars,
            expand_dependencies=self.cfg.graphlocator.expand_dependencies_in_causal,
        )
        cig = timed_value(
            stage_times,
            "dynamic_cig_discovering",
            lambda: causal_agent.generate_causal_graph(ctx.problem_statement, seed_locations),
        )
        updated_graph = search_agent.repo_graph.repo_graph
        loc_results = timed_value(
            stage_times,
            "convert_results",
            lambda: convert_results(cig, updated_graph, self.cfg.paths.gcc_repo_dir),
        )

        found_files = unique_keep_order(loc_results.get("found_files", []))
        found_methods = unique_method_items(loc_results.get("found_function_items", []))
        final_top10 = found_files[: self.cfg.graphlocator.file_top_n]
        final_methods = found_methods[: self.cfg.graphlocator.method_top_n]
        top_files = [{"path": path, "rank": idx} for idx, path in enumerate(final_top10, start=1)]
        top_methods = [dict(item, rank=idx) for idx, item in enumerate(final_methods, start=1)]

        report: Dict[str, Any] = {
            "bug_id": bug_id,
            "checkout": {
                "requested": ctx.base_commit,
                "resolved": checkout_commit,
            },
            "final_top10": final_top10,
            "final_method_top10": [item["id"] for item in final_methods],
            "final_method_items": top_methods,
            "file_level": {
                "found_files": found_files,
                "top_files": top_files,
            },
            "method_level": {
                "found_functions": loc_results.get("found_functions", []),
                "found_methods": found_methods,
                "top_methods": top_methods,
            },
            "graphlocator": {
                "model": self.cfg.llm.model,
                "graph_cache_path": str(graph_result.cache_path),
                "graph_cache_hit": graph_result.cache_hit,
                "graph_cache_build_mode": graph_result.build_mode,
                "graph_cache_bug_id": graph_result.bug_id,
                "graph_cache_checkout_commit": graph_result.checkout_commit,
                "initial_graph_node_count": initial_graph_node_count,
                "initial_graph_edge_count": initial_graph_edge_count,
                "graph_node_count": updated_graph.graph.number_of_nodes(),
                "graph_edge_count": updated_graph.graph.number_of_edges(),
                "lazy_loaded_edge_count": max(0, updated_graph.graph.number_of_edges() - initial_graph_edge_count),
                "search_topk": self.cfg.graphlocator.search_topk,
                "max_search_turn": self.cfg.graphlocator.max_search_turn,
                "max_causal_turn": self.cfg.graphlocator.max_causal_turn,
                "max_causal_code_elements": self.cfg.graphlocator.max_causal_code_elements,
                "max_causal_node_content_chars": self.cfg.graphlocator.max_causal_node_content_chars,
                "max_existing_node_content_chars": self.cfg.graphlocator.max_existing_node_content_chars,
                "max_causal_prompt_chars": self.cfg.graphlocator.max_causal_prompt_chars,
                "expand_dependencies_in_search": self.cfg.graphlocator.expand_dependencies_in_search,
                "expand_dependencies_in_causal": self.cfg.graphlocator.expand_dependencies_in_causal,
                "dependency_expansion": search_agent.repo_graph.stats(),
                "seed_locations": [node_to_json(node) for node in seed_locations],
                "found_files": found_files,
                "found_modules": loc_results.get("found_modules", []),
                "found_functions": loc_results.get("found_functions", []),
                "cig": serialize_cig(cig, self.cfg.paths.gcc_repo_dir),
            },
            "efficiency": {
                "total_wall_time_sec": round(time.time() - total_start, 3),
                "stage_times_sec": {key: round(value, 3) for key, value in stage_times.items()},
                "llm_usage": llm.usage_tracker.to_dict(start_index=usage_start_index),
            },
        }

        ensure_dir(self.cfg.report_dir)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report


def timed_value(stage_times: Dict[str, float], key: str, fn: Callable[[], Any]) -> Any:
    start = time.time()
    value = fn()
    stage_times[key] = time.time() - start
    return value


def unique_method_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        method_id = str(item.get("id") or "").strip()
        if not method_id or method_id in seen:
            continue
        seen.add(method_id)
        out.append(item)
    return out
