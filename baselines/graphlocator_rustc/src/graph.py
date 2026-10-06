from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rdfs.dependency_graph.dependency_graph import DependencyGraph
from rdfs.dependency_graph.models.graph_data import Node
from rdfs.dependency_graph.graph_generator.tree_sitter_generator import TreeSitterDependencyGraphGenerator
from rdfs.dependency_graph.models.language import Language
from rdfs.dependency_graph.models.repository import Repository

from config import RuntimeConfig
from context import ensure_dir


@dataclass(frozen=True)
class GraphBuildResult:
    graph: DependencyGraph
    cache_path: Path
    cache_hit: bool
    build_mode: str
    bug_id: str
    checkout_commit: str


class IncrementalRDFS:
    def __init__(self, repo_graph: DependencyGraph):
        self.repo_graph = repo_graph
        self.updated_files: set[str] = set()
        self.graph_generator = TreeSitterDependencyGraphGenerator()

    def incremental_add_dependency(self, node: Any) -> None:
        if not getattr(node, "location", None) or not node.location.file_path:
            return
        file_path = str(node.location.file_path)
        if file_path in self.updated_files:
            return
        self.updated_files.add(file_path)
        self.repo_graph = self.graph_generator.incremental_update_graph_with_dependency(
            self.repo_graph,
            [file_path],
        )

    def find_equivalent_node(self, node: Any) -> Any | None:
        graph = self.repo_graph.graph
        if graph.has_node(node):
            return node
        if not isinstance(node, Node):
            return None

        target_path = str(node.location.file_path) if node.location and node.location.file_path else ""
        target_loc = (
            target_path,
            node.location.start_line if node.location else None,
            node.location.start_column if node.location else None,
            node.location.end_line if node.location else None,
            node.location.end_column if node.location else None,
        )
        for candidate in graph.nodes:
            if not isinstance(candidate, Node):
                continue
            candidate_path = str(candidate.location.file_path) if candidate.location and candidate.location.file_path else ""
            candidate_loc = (
                candidate_path,
                candidate.location.start_line if candidate.location else None,
                candidate.location.start_column if candidate.location else None,
                candidate.location.end_line if candidate.location else None,
                candidate.location.end_column if candidate.location else None,
            )
            if candidate.type == node.type and candidate.name == node.name and candidate_loc == target_loc:
                return candidate
        return None

    def one_hop_neighbors(self, node: Any) -> list[Any]:
        graph = self.repo_graph.graph
        graph_node = self.find_equivalent_node(node)
        if graph_node is None or not graph.has_node(graph_node):
            return []
        return list(set(graph.predecessors(graph_node)) | set(graph.successors(graph_node)))


def load_or_build_compiler_graph(cfg: RuntimeConfig, *, bug_id: str, checkout_commit: str) -> GraphBuildResult:
    ensure_dir(cfg.graph_cache_dir)
    cache_path = cfg.graph_cache_dir / f"{bug_id}.json"
    if cache_path.exists():
        graph = DependencyGraph.from_json(cache_path.read_text(encoding="utf-8", errors="replace"))
        return GraphBuildResult(
            graph=graph,
            cache_path=cache_path,
            cache_hit=True,
            build_mode="bug_cache",
            bug_id=bug_id,
            checkout_commit=checkout_commit,
        )

    compiler_root = cfg.paths.rust_repo_dir / "compiler"
    if not compiler_root.exists():
        raise FileNotFoundError(f"rust compiler directory not found: {compiler_root}")

    # GraphLocator builds the RDFS membership skeleton up front and lazily
    # adds expensive dependency edges when agents traverse selected files.
    repo = Repository(compiler_root, Language.Rust)
    graph = TreeSitterDependencyGraphGenerator().generate(repo)
    cache_path.write_text(graph.to_json(indent=2), encoding="utf-8")
    return GraphBuildResult(
        graph=graph,
        cache_path=cache_path,
        cache_hit=False,
        build_mode="skeleton",
        bug_id=bug_id,
        checkout_commit=checkout_commit,
    )
