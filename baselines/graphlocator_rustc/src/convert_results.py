from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List

from rdfs.dependency_graph.dependency_graph import DependencyGraph
from rdfs.dependency_graph.models.graph_data import Node, NodeType


def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def normalize_compiler_file_path(file_path: Any, rust_repo_dir: Path) -> str:
    if not file_path:
        return ""
    path = Path(str(file_path)).resolve()
    rust_repo_dir = rust_repo_dir.resolve()
    compiler_root = rust_repo_dir / "compiler"
    try:
        return path.relative_to(rust_repo_dir).as_posix()
    except ValueError:
        pass
    try:
        return (Path("compiler") / path.relative_to(compiler_root)).as_posix()
    except Exception:
        pass
    text = str(file_path).replace("\\", "/")
    marker = "/compiler/"
    if marker in text:
        return "compiler/" + text.split(marker, 1)[1]
    if text.startswith("compiler/"):
        return text
    return text


def node_type_value(node: Node) -> str:
    return node.type.value if hasattr(node.type, "value") else str(node.type)


def reformat_entity_name(rdfs_graph: DependencyGraph, node: Node, rust_repo_dir: Path) -> str:
    if not node.location or not node.location.file_path:
        return ""
    file_path = normalize_compiler_file_path(node.location.file_path, rust_repo_dir)
    if not file_path.startswith("compiler/") or not file_path.endswith(".rs"):
        return ""

    node_type = node_type_value(node)
    if node_type in {NodeType.METHOD.value, NodeType.FUNCTION.value, NodeType.CONSTRUCTOR.value}:
        parent = safe_membership_parent(rdfs_graph, node)
        parent_type = node_type_value(parent) if parent else ""
        if parent and parent_type in {
            NodeType.CLASS.value,
            NodeType.INTERFACE.value,
            NodeType.ENUM.value,
            NodeType.STRUCTURE.value,
        } and parent.name != node.name:
            return f"{file_path}::{parent.name}.{node.name}"
        return f"{file_path}::{node.name}"

    if node_type in {
        NodeType.CLASS.value,
        NodeType.INTERFACE.value,
        NodeType.ENUM.value,
        NodeType.STRUCTURE.value,
        NodeType.FIELD.value,
        NodeType.GLOBAL_VAR.value,
    }:
        return f"{file_path}::{node.name}"

    if node_type == NodeType.FILE.value:
        return file_path
    return file_path


def convert_results(
    cig_graph,
    rdfs_graph: DependencyGraph,
    rust_repo_dir: Path,
    *,
    include_methods: bool = True,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "found_files": [],
        "found_modules": [],
        "found_functions": [],
        "found_function_items": [],
    }
    for factor in cig_graph.nodes:
        node_list = cig_graph.nodes[factor].get("code_element") or []
        for code_entity in node_list:
            entity_name = reformat_entity_name(rdfs_graph, code_entity, rust_repo_dir)
            if not entity_name:
                continue
            file_path = entity_name.split("::", 1)[0]
            if file_path not in results["found_files"]:
                results["found_files"].append(file_path)

            node_type = node_type_value(code_entity)
            if "::" in entity_name and node_type in {
                NodeType.CLASS.value,
                NodeType.INTERFACE.value,
                NodeType.ENUM.value,
                NodeType.STRUCTURE.value,
                NodeType.FIELD.value,
                NodeType.GLOBAL_VAR.value,
            }:
                if entity_name not in results["found_modules"]:
                    results["found_modules"].append(entity_name)
            elif "::" in entity_name and node_type in {
                NodeType.METHOD.value,
                NodeType.FUNCTION.value,
                NodeType.CONSTRUCTOR.value,
            }:
                if not include_methods:
                    continue
                if entity_name not in results["found_functions"]:
                    results["found_functions"].append(entity_name)
                method_item = method_item_from_node(
                    rdfs_graph=rdfs_graph,
                    node=code_entity,
                    entity_name=entity_name,
                    rust_repo_dir=rust_repo_dir,
                    rank=len(results["found_function_items"]) + 1,
                )
                if method_item and not any(item.get("id") == method_item["id"] for item in results["found_function_items"]):
                    results["found_function_items"].append(method_item)
                entity_tail = entity_name.split("::", 1)[1]
                if "." in entity_tail:
                    module_name = entity_name.rsplit(".", 1)[0]
                    if module_name not in results["found_modules"]:
                        results["found_modules"].append(module_name)
    return results


def safe_membership_parent(rdfs_graph: DependencyGraph, node: Node) -> Node | None:
    try:
        return rdfs_graph.get_membership_parent(node)
    except Exception:
        return None


def method_item_from_node(
    *,
    rdfs_graph: DependencyGraph,
    node: Node,
    entity_name: str,
    rust_repo_dir: Path,
    rank: int,
) -> Dict[str, Any]:
    file_path = entity_name.split("::", 1)[0]
    qualified_name = entity_name.split("::", 1)[1] if "::" in entity_name else node.name
    location = node.location
    start_line = location.start_line if location else None
    end_line = location.end_line if location else None
    node_type = node_type_value(node)
    item_type = {
        NodeType.METHOD.value: "method",
        NodeType.FUNCTION.value: "function",
        NodeType.CONSTRUCTOR.value: "method",
    }.get(node_type, node_type.lower())
    parent = safe_membership_parent(rdfs_graph, node)
    parent_name = parent.name if parent else ""
    method_id = f"{file_path}::{qualified_name}@{start_line}-{end_line}"
    return {
        "id": method_id,
        "rank": rank,
        "file": file_path,
        "item_type": item_type,
        "item_name": node.name,
        "qualified_name": qualified_name,
        "parent_name": parent_name,
        "start_line": start_line,
        "end_line": end_line,
        "source": "graphlocator_cig",
    }


def node_report(node: Node, rust_repo_dir: Path) -> Dict[str, Any]:
    return {
        "type": node_type_value(node),
        "name": node.name,
        "file": normalize_compiler_file_path(node.location.file_path, rust_repo_dir)
        if node.location and node.location.file_path else "",
        "content": node.content,
    }


def serialize_cig(cig_graph, rust_repo_dir: Path) -> Dict[str, Any]:
    nodes = []
    for node_id in cig_graph.nodes:
        data = cig_graph.nodes[node_id]
        nodes.append({
            "id": node_id,
            "name": data.get("name", ""),
            "code_elements": [
                node_report(code_entity, rust_repo_dir)
                for code_entity in data.get("code_element", [])
            ],
        })
    edges = [
        {"source": src, "target": trg, "weight": data.get("weight", 1.0)}
        for src, trg, data in cig_graph.edges(data=True)
    ]
    return {"nodes": nodes, "edges": edges}
