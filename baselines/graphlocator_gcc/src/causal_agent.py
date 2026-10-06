from __future__ import annotations

import re
from ast import literal_eval
from typing import Any, Dict, List

import networkx as nx

from llm import GraphLocatorLLM
from prompts.causal_agent_prompt import (
    GENERATE_CAUSAL_GRAPH_INSTRUCTION,
    SYSTEM_PROMPT,
    UPDATE_CAUSAL_GRAPH_INSTRUCTION,
)
from utils.string_processing import node_to_json, truncate_text


class CausalAgent:
    def __init__(
        self,
        llm: GraphLocatorLLM,
        repo_graph,
        *,
        max_turn: int = 20,
        max_code_elements: int = 80,
        node_content_chars: int = 4000,
        existing_node_content_chars: int = 1200,
        max_prompt_chars: int = 350000,
        expand_dependencies: bool = True,
    ) -> None:
        self.llm = llm
        self.causal_graph = nx.DiGraph()
        self.repo_graph = repo_graph
        self.max_turn = max_turn
        self.max_code_elements = max(1, max_code_elements)
        self.node_content_chars = max(1, node_content_chars)
        self.existing_node_content_chars = max(1, existing_node_content_chars)
        self.max_prompt_chars = max(1, max_prompt_chars)
        self.expand_dependencies = expand_dependencies
        self.messages: List[Dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    def parse_mermaid_to_networkx(self, mermaid_str: str, node_list: List[Any]):
        selected_nodes = []
        graph = nx.DiGraph()
        pattern = re.compile(
            r"(?P<source>\w+)\[(?P<source_name>[^<]+)<br>Code Elements: \[(?P<source_code>[^\]]*)\]\]"
            r"\s*-->\|(?P<weight>[\d.]+)\|\s*"
            r"(?P<target>\w+)(?:\[[^\]]*\])?"
        )
        for raw_line in mermaid_str.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("graph") or line.startswith("```"):
                continue
            match = pattern.match(line)
            if not match:
                continue
            groups = match.groupdict()
            code_elements = []
            if groups["source"] not in self.causal_graph and groups["source_code"]:
                try:
                    code_element_ids = literal_eval(f"[{groups['source_code']}]")
                    if not isinstance(code_element_ids, list):
                        code_element_ids = [code_element_ids]
                    for code_element_id in code_element_ids:
                        node = node_list[int(code_element_id) - 1]
                        code_elements.append(node)
                        selected_nodes.append(node)
                except Exception:
                    code_elements = []
            if groups["source"] not in graph:
                graph.add_node(groups["source"], name=groups["source_name"].strip(), code_element=code_elements)
            if groups["target"] not in graph:
                graph.add_node(groups["target"], name="Issue", code_element=[])
            graph.add_edge(groups["source"], groups["target"], weight=float(groups["weight"]))

        if len(self.causal_graph.nodes) != 0:
            for node_id in self.causal_graph.nodes:
                if node_id in graph.nodes:
                    graph.nodes[node_id]["name"] = self.causal_graph.nodes[node_id]["name"]
                    for element in self.causal_graph.nodes[node_id]["code_element"]:
                        if element not in graph.nodes[node_id]["code_element"]:
                            graph.nodes[node_id]["code_element"].append(element)
                else:
                    graph.add_node(
                        node_id,
                        name=self.causal_graph.nodes[node_id]["name"],
                        code_element=self.causal_graph.nodes[node_id]["code_element"],
                    )
            for src, trg, data in self.causal_graph.edges(data=True):
                if not graph.has_edge(src, trg):
                    graph.add_edge(src, trg, weight=data.get("weight", 1.0))

        new_factors = [node_id for node_id in graph.nodes if node_id not in self.causal_graph.nodes]
        return graph, new_factors, selected_nodes

    def networkx_to_mermaid(self, *, content_limit: int | None = None) -> str:
        mermaid_lines = ["graph TD"]
        processed_nodes = set()
        for src, trg, data in self.causal_graph.edges(data=True):
            weight = data.get("weight", 1.0)
            if src not in processed_nodes:
                src_name = self.causal_graph.nodes[src].get("name", f"Node {src}")
                mermaid_lines.append(f"    {src}[{src_name}] -->|{weight}| ")
                processed_nodes.add(src)
            else:
                mermaid_lines.append(f"    {src} -->|{weight}| ")

            if trg not in processed_nodes:
                trg_name = self.causal_graph.nodes[trg].get("name", f"Node {trg}")
                mermaid_lines[-1] += f"{trg}[{trg_name}]"
                processed_nodes.add(trg)
            else:
                mermaid_lines[-1] += f"{trg}"

        for node_id in self.causal_graph.nodes:
            mermaid_lines.append(f"Node {node_id}:")
            for code_element in self.causal_graph.nodes[node_id].get("code_element", []):
                if code_element.location:
                    content = truncate_text(code_element.content or "", content_limit)
                    mermaid_lines.append(f"Location in repository: {code_element.location.file_path}")
                    mermaid_lines.append(f"Name and content: {code_element.name}\n{content}")
        return "\n".join(mermaid_lines)

    def generate_causal_graph(self, issue_description: str, node_list: List[Any]):
        initial_prompt, prompt_node_list = self._build_initial_prompt(issue_description, node_list)
        self.messages.append({
            "role": "user",
            "content": initial_prompt,
        })
        response, _finish_reason, _usage = self.llm.completion(self.messages, stage="causal_initial")
        self.messages.append({"role": "assistant", "content": response[0].get("content")})
        self.causal_graph, _new_factors, selected_nodes = self.parse_mermaid_to_networkx(
            str(response[0].get("content") or ""),
            prompt_node_list,
        )

        visited = set(selected_nodes)
        priority_queue = self._build_priority_queue()
        turn = 0
        while priority_queue:
            if turn >= self.max_turn:
                print("Max causal turn reached, stopping.", flush=True)
                break
            factor, _score = priority_queue.pop(0)
            to_be_visited_nodes = []
            for node in self.causal_graph.nodes[factor].get("code_element", []):
                if self.expand_dependencies:
                    self.repo_graph.incremental_add_dependency(node)
                for neighbor in self.repo_graph.one_hop_neighbors(node):
                    if neighbor not in to_be_visited_nodes and neighbor not in visited:
                        to_be_visited_nodes.append(neighbor)

            update_prompt, prompt_nodes = self._build_update_prompt(
                issue_description,
                factor,
                to_be_visited_nodes,
            )
            self.messages.append({"role": "user", "content": update_prompt})
            response, _finish_reason, _usage = self.llm.completion(
                [{"role": "user", "content": update_prompt}],
                stage=f"causal_update_{turn + 1}",
            )
            self.messages.append({"role": "assistant", "content": response[0].get("content")})
            self.causal_graph, new_factors, selected_nodes = self.parse_mermaid_to_networkx(
                str(response[0].get("content") or ""),
                prompt_nodes,
            )
            turn += 1

            for new_factor in new_factors:
                prob_list = [
                    data.get("weight", 1.0)
                    for _src, _trg, data in self.causal_graph.out_edges(new_factor, data=True)
                ]
                if prob_list:
                    priority_queue.append((new_factor, max(prob_list)))
            for node in selected_nodes:
                visited.add(node)
            priority_queue.sort(key=lambda item: -item[1])
        return self.causal_graph

    def _build_initial_prompt(self, issue_description: str, node_list: List[Any]) -> tuple[str, List[Any]]:
        prompt_nodes = self._select_prompt_nodes(node_list)
        original_count = len(node_list)
        content_limit = self.node_content_chars
        initial_limit = content_limit

        while True:
            element_str_list = self._format_code_elements(prompt_nodes, content_limit)
            prompt = (
                f"{GENERATE_CAUSAL_GRAPH_INSTRUCTION}\n"
                f"# Issue Description:\n{issue_description}\n"
                f"# Code Element List:\n{element_str_list}"
            )
            if len(prompt) <= self.max_prompt_chars:
                break
            next_nodes, next_limit = self._shrink_nodes_and_content(prompt_nodes, content_limit)
            if next_nodes == prompt_nodes and next_limit == content_limit:
                break
            prompt_nodes, content_limit = next_nodes, next_limit

        self._log_prompt_budget("initial", original_count, len(prompt_nodes), initial_limit, content_limit, len(prompt))
        return prompt, prompt_nodes

    def _build_update_prompt(
        self,
        issue_description: str,
        factor: str,
        to_be_visited_nodes: List[Any],
    ) -> tuple[str, List[Any]]:
        prompt_nodes = self._select_prompt_nodes(to_be_visited_nodes)
        original_count = len(to_be_visited_nodes)
        content_limit = self.node_content_chars
        existing_limit = self.existing_node_content_chars
        initial_content_limit = content_limit

        while True:
            element_str_list = self._format_code_elements(prompt_nodes, content_limit)
            prompt = (
                f"{UPDATE_CAUSAL_GRAPH_INSTRUCTION}\n"
                f"# Issue Description:\n{issue_description}\n"
                f"#Existing causal graph:\n{self.networkx_to_mermaid(content_limit=existing_limit)}\n"
                f"# Factor to be refined:{factor} [{self.causal_graph.nodes[factor]['name']}]\n"
                f"# New Code Element List:\n{element_str_list}"
            )
            if len(prompt) <= self.max_prompt_chars:
                break
            if existing_limit > 300:
                existing_limit = max(300, existing_limit // 2)
                continue
            next_nodes, next_limit = self._shrink_nodes_and_content(prompt_nodes, content_limit)
            if next_nodes == prompt_nodes and next_limit == content_limit:
                break
            prompt_nodes, content_limit = next_nodes, next_limit

        self._log_prompt_budget("update", original_count, len(prompt_nodes), initial_content_limit, content_limit, len(prompt))
        return prompt, prompt_nodes

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

    def _format_code_elements(self, nodes: List[Any], content_limit: int) -> List[str]:
        return [
            f"#Code element {idx + 1}: {node_to_json(node, content_limit=content_limit)}"
            for idx, node in enumerate(nodes)
        ]

    @staticmethod
    def _shrink_nodes_and_content(nodes: List[Any], content_limit: int) -> tuple[List[Any], int]:
        if content_limit > 500:
            return nodes, max(500, content_limit // 2)
        if len(nodes) > 10:
            return nodes[: max(10, len(nodes) // 2)], content_limit
        return nodes, content_limit

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

    def _log_prompt_budget(
        self,
        stage: str,
        original_count: int,
        selected_count: int,
        initial_content_limit: int,
        final_content_limit: int,
        prompt_chars: int,
    ) -> None:
        if (
            original_count != selected_count
            or initial_content_limit != final_content_limit
            or prompt_chars > self.max_prompt_chars
        ):
            print(
                "[causal-agent] "
                f"{stage} prompt budget: nodes {original_count}->{selected_count}, "
                f"content chars {initial_content_limit}->{final_content_limit}, "
                f"prompt chars={prompt_chars}/{self.max_prompt_chars}",
                flush=True,
            )

    def _build_priority_queue(self) -> List[tuple[str, float]]:
        priority_queue: List[tuple[str, float]] = []
        for node_id in self.causal_graph.nodes:
            if node_id == "I":
                continue
            scores = [
                data.get("weight", 1.0)
                for _src, _trg, data in self.causal_graph.out_edges(node_id, data=True)
            ]
            if scores:
                priority_queue.append((node_id, max(scores)))
        priority_queue.sort(key=lambda item: -item[1])
        return priority_queue
