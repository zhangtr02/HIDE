from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from rdfs.dependency_graph.dependency_graph import DependencyGraph

from graph import DependencyExpansionOptions, IncrementalRDFS
from llm import GraphLocatorLLM
from prompts.search_agent_prompt import (
    FinishTool,
    GET_SEED_LOC_INSTRUCTION,
    IS_RELEVANT_INSTRUCTION,
    SearchEdgeTool,
    SearchNodeTool,
)
from search_tools import search_edge, search_node
from utils.string_processing import edge_deserialization, node_deserialization, node_to_json


class SearchAgent:
    def __init__(
        self,
        repo_graph: DependencyGraph,
        llm: GraphLocatorLLM,
        *,
        max_search_turn: int = 5,
        top_k: int = 5,
        max_code_elements: int = 80,
        node_content_chars: int = 4000,
        expand_dependencies: bool = False,
        dependency_options: DependencyExpansionOptions | None = None,
    ) -> None:
        self.repo_graph = IncrementalRDFS(repo_graph, dependency_options)
        self.llm = llm
        self.max_search_turn = max_search_turn
        self.top_k = top_k
        self.max_code_elements = max(1, max_code_elements)
        self.node_content_chars = max(1, node_content_chars)
        self.expand_dependencies = expand_dependencies
        self.messages: List[Dict[str, Any]] = []
        self.search_res: set[Any] = set()

    def get_seed_location(self, issue_description: str) -> List[Any]:
        self.messages.append({"role": "user", "content": GET_SEED_LOC_INSTRUCTION})
        self.messages.append({"role": "user", "content": f"### GitHub Problem Description ###\n{issue_description}"})
        finish = False
        for turn in range(self.max_search_turn):
            print(f"Searching for seed locations, turn {turn + 1}/{self.max_search_turn}", flush=True)
            response, _finish_reason, _usage = self.llm.completion(
                self.messages,
                tools=[SearchNodeTool, SearchEdgeTool, FinishTool],
                stage=f"search_seed_turn_{turn + 1}",
            )
            assistant_message = response[0]
            self.messages.append(assistant_message)
            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                break

            for tool_call in tool_calls:
                if self._tool_name(tool_call) == "finish":
                    finish = True
                    continue
                self.run_search(tool_call, issue_description)
            if finish:
                break
        return list(self.search_res)

    def run_search(self, tool_call: Dict[str, Any], issue_description: str) -> None:
        function_name = self._tool_name(tool_call)
        arguments = self._tool_arguments(tool_call)
        function_response_selected = ""

        if function_name == "search_node":
            function_response = search_node(
                self.repo_graph.repo_graph.graph,
                node_type=str(arguments.get("node_type") or "*"),
                node_name=str(arguments.get("node_name") or "*"),
                top_k=self.top_k,
            )
            selected = node_deserialization(function_response)
        elif function_name == "search_edge":
            function_response = search_edge(
                self.repo_graph.repo_graph.graph,
                src_node_type=str(arguments.get("src_node_type") or "*"),
                src_node_name=str(arguments.get("src_node_name") or "*"),
                edge_type=str(arguments.get("edge_type") or "*"),
                trg_node_type=str(arguments.get("trg_node_type") or "*"),
                trg_node_name=str(arguments.get("trg_node_name") or "*"),
            )
            selected = []
            for src_node, trg_node, _edge in edge_deserialization(function_response):
                if src_node not in selected:
                    selected.append(src_node)
                if trg_node not in selected:
                    selected.append(trg_node)
        else:
            return

        original_selected_count = len(selected)
        selected = self._select_prompt_nodes(selected)
        if original_selected_count != len(selected):
            print(
                "[search-agent] candidate budget: "
                f"nodes {original_selected_count}->{len(selected)}",
                flush=True,
            )

        if len(selected) >= self.top_k:
            relevant = self.is_relevant(
                [
                    f"#Node {idx + 1}: {node_to_json(node, content_limit=self.node_content_chars)}"
                    for idx, node in enumerate(selected)
                ],
                issue_description,
            )
            if len(relevant) != len(selected):
                selected = []
            else:
                selected = [node for idx, node in enumerate(selected) if relevant[idx]]

        for node in selected:
            self.search_res.add(node)
            function_response_selected += node_to_json(node, content_limit=self.node_content_chars) + "\n"
            if self.expand_dependencies:
                self.repo_graph.incremental_add_dependency(node)

        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call.get("id"),
            "name": function_name,
            "content": "OBSERVATION:\n" + function_response_selected,
        })

    def is_relevant(self, node_list: List[str], issue_description: str) -> List[bool]:
        node_list_str = "\n".join(node_list)
        relevance_messages = [
            {"role": "user", "content": IS_RELEVANT_INSTRUCTION},
            {
                "role": "user",
                "content": f"# Issue Description:\n{issue_description}\n###\n"
                           f"### Code Elements List ###\n{node_list_str}\n###\n",
            },
        ]
        response, _finish_reason, _usage = self.llm.completion(relevance_messages, stage="search_relevance")
        assistant_message = response[0]
        content = str(assistant_message.get("content") or "")
        pattern = r"```(?:\w+)?\n(.*?)\n```"
        blocks = re.findall(pattern, content, re.DOTALL)
        res_block = "\n".join(blocks) if blocks else content
        return [line.strip() == "True" for line in res_block.splitlines() if line.strip()]

    @staticmethod
    def _tool_name(tool_call: Dict[str, Any]) -> str:
        function_obj = tool_call.get("function") or {}
        return str(function_obj.get("name") or "")

    @staticmethod
    def _tool_arguments(tool_call: Dict[str, Any]) -> Dict[str, Any]:
        function_obj = tool_call.get("function") or {}
        raw_args = function_obj.get("arguments") or "{}"
        if isinstance(raw_args, dict):
            return raw_args
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return {}

    def _select_prompt_nodes(self, nodes: List[Any]) -> List[Any]:
        unique_nodes = []
        seen = set()
        for node in nodes:
            if node in seen:
                continue
            seen.add(node)
            unique_nodes.append(node)
        unique_nodes.sort(key=self._node_sort_key)
        return unique_nodes[: self.max_code_elements]

    @staticmethod
    def _node_sort_key(node: Any) -> tuple[int, str, int, str]:
        node_type = node.type.value if hasattr(getattr(node, "type", ""), "value") else str(getattr(node, "type", ""))
        priority = {
            "FUNCTION": 0,
            "METHOD": 0,
            "CONSTRUCTOR": 0,
            "STRUCTURE": 1,
            "ENUM": 1,
            "CLASS": 1,
            "INTERFACE": 1,
            "GLOBAL_VAR": 1,
            "FIELD": 1,
            "FILE": 2,
            "DIRECTORY": 3,
            "PACKAGE": 3,
            "REPO": 4,
        }.get(node_type, 5)
        location = getattr(node, "location", None)
        file_path = str(location.file_path) if location and location.file_path else ""
        start_line = location.start_line if location and location.start_line else 0
        return (priority, file_path, start_line, str(getattr(node, "name", "")))
