from __future__ import annotations

import json
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, List

import networkx as nx

from rdfs.dependency_graph.models.graph_data import Node, NodeType


def string_distance(str1: str, str2: str) -> float:
    str1 = (str1 or "").lower()
    str2 = (str2 or "").lower()
    for suffix in (".py", ".java", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"):
        if str1.endswith(suffix):
            str1 = str1.removesuffix(suffix)
        if str2.endswith(suffix):
            str2 = str2.removesuffix(suffix)
    if not str1 or not str2:
        return 1.0
    if str1 in str2 or str2 in str1:
        return 0.0
    return 1.0 - SequenceMatcher(None, str1, str2).ratio()


def normalize_node_type(node_type: str | None) -> str:
    value = (node_type or "*").strip().upper()
    aliases = {
        "METHOD": "FUNCTION",
        "STRUCT": "STRUCTURE",
        "CONST": "GLOBAL_VAR",
        "CONSTANT": "GLOBAL_VAR",
    }
    return aliases.get(value, value)


def node_type_value(node: Node) -> str:
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def node_matches_name(node: Node, node_name: str) -> bool:
    if node_name == "*":
        return True
    name = node.name or ""
    basename = Path(name).name
    stem = Path(name).stem
    return node_name in {name, basename, stem} or name.split(".")[0] == node_name


def node_json_lines(nodes: Iterable[Node]) -> str:
    return "\n".join(node.to_json() for node in nodes)


def search_node(graph: nx.MultiDiGraph, node_type: str, node_name: str, top_k: int = 5) -> str:
    node_type = normalize_node_type(node_type)
    node_name = (node_name or "*").strip()

    if node_type == "*" and node_name == "*":
        raise ValueError("Search Error: both the node type and node name are wildcard.")

    graph_nodes = list(graph.nodes)
    if node_type == "BODY":
        candidates = [
            v for v in graph_nodes
            if node_type_value(v) in {"METHOD", "FUNCTION"} and node_name != "*" and node_name in (v.content or "")
        ]
        if not candidates and node_name != "*":
            ranked = [
                (v, string_distance(v.content or "", node_name))
                for v in graph_nodes
                if node_type_value(v) in {"METHOD", "FUNCTION"}
            ]
            ranked.sort(key=lambda item: item[1])
            candidates = [item[0] for item in ranked[:top_k]]
        return node_json_lines(candidates)

    candidates = [
        v for v in graph_nodes
        if (node_type == "*" or node_type_value(v) == node_type)
        and node_matches_name(v, node_name)
    ]
    if not candidates and node_name != "*":
        ranked = [
            (v, string_distance(v.name or "", node_name))
            for v in graph_nodes
            if node_type == "*" or node_type_value(v) == node_type
        ]
        ranked.sort(key=lambda item: item[1])
        candidates = [item[0] for item in ranked[:top_k]]
    return node_json_lines(candidates)


def search_edge(
    graph: nx.MultiDiGraph,
    src_node_type: str = "*",
    src_node_name: str = "*",
    edge_type: str = "*",
    trg_node_type: str = "*",
    trg_node_name: str = "*",
) -> str:
    src_node_type = normalize_node_type(src_node_type)
    trg_node_type = normalize_node_type(trg_node_type)
    src_node_name = (src_node_name or "*").strip()
    trg_node_name = (trg_node_name or "*").strip()
    edge_type = (edge_type or "*").strip()
    edge_list: List[str] = []

    if trg_node_type == "BODY" and edge_type in {"*", "HasMember"}:
        if trg_node_name == "*":
            return ""
        for node in graph.nodes:
            if node_type_value(node) not in {"METHOD", "FUNCTION"}:
                continue
            if not node_matches_name(node, src_node_name):
                continue
            if trg_node_name not in (node.content or ""):
                continue
            edge_list.append(json.dumps({
                "src_node": node.to_json(),
                "trg_node": node.to_json(),
                "edge": {"relation": "HasMember"},
            }))
        return "\n".join(edge_list)

    for src_node, trg_node, relation_obj in graph.edges(data="relation"):
        relation = relation_obj.relation.value
        curr_src_type = "FUNCTION" if node_type_value(src_node) in {"METHOD", "FUNCTION"} else node_type_value(src_node)
        curr_trg_type = "FUNCTION" if node_type_value(trg_node) in {"METHOD", "FUNCTION"} else node_type_value(trg_node)
        if src_node_type != "*" and curr_src_type != src_node_type:
            continue
        if trg_node_type != "*" and curr_trg_type != trg_node_type:
            continue
        if edge_type != "*" and relation != edge_type:
            continue
        if not node_matches_name(src_node, src_node_name):
            continue
        if not node_matches_name(trg_node, trg_node_name):
            continue
        edge_list.append(json.dumps({
            "src_node": src_node.to_json(),
            "trg_node": trg_node.to_json(),
            "edge": relation_obj.to_json(),
        }))
    return "\n".join(edge_list)
