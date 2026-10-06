from __future__ import annotations

import json
import os
import re
from typing import List, Tuple

from rdfs.dependency_graph.models.graph_data import Edge, Node


def truncate_text(text: str, max_chars: int | None) -> str:
    if max_chars is None or max_chars <= 0 or len(text) <= max_chars:
        return text
    marker = f"\n... [truncated {len(text) - max_chars} chars] ...\n"
    keep = max(0, max_chars - len(marker))
    head = keep // 2
    tail = keep - head
    return text[:head] + marker + (text[-tail:] if tail else "")


def node_to_json(node: Node, *, content_limit: int | None = None) -> str:
    node_dict = {
        "type": node.type.value if hasattr(node.type, "value") else node.type,
        "name": node.name,
        "location": str(node.location.file_path) if node.location else None,
        "content": truncate_text(node.content or "", content_limit),
    }
    return json.dumps(node_dict, ensure_ascii=False)


def node_deserialization(node_list_str: str | None) -> List[Node]:
    if not node_list_str:
        return []
    res: List[Node] = []
    for node_str in node_list_str.splitlines(keepends=False):
        try:
            res.append(Node(type=None, name=None, location=None).from_json(node_str))
        except Exception:
            pass
    return res


def edge_deserialization(edge_list_str: str | None) -> List[Tuple[Node, Node, Edge]]:
    if not edge_list_str:
        return []
    res: List[Tuple[Node, Node, Edge]] = []
    for edge_str in edge_list_str.splitlines(keepends=False):
        if not edge_str:
            continue
        try:
            edge_obj = json.loads(edge_str)
            res.append((
                Node(type=None, name=None, location=None).from_json(edge_obj["src_node"]),
                Node(type=None, name=None, location=None).from_json(edge_obj["trg_node"]),
                Edge(relation=None, location=None).from_json(edge_obj["edge"]),
            ))
        except Exception:
            continue
    return res


def calculate_import_path(current_file, import_string):
    dots_match = re.match(r"(\.+)(.*)", import_string)
    if not dots_match:
        raise ValueError(f"invalid relative import string: {import_string}")

    dots, module_path = dots_match.groups()
    dot_count = len(dots)
    current_dir = os.path.dirname(str(current_file))
    parent_dir = current_dir
    for _ in range(dot_count - 1):
        parent_dir = os.path.dirname(parent_dir)

    module_parts = module_path.split(".")
    module_file = module_parts[-1] + ".py"
    module_dir = os.path.join(parent_dir, *module_parts[:-1])
    return os.path.join(module_dir, module_file)
