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


@dataclass(frozen=True)
class DependencyExpansionOptions:
    max_files: int = 4
    max_nodes_per_file: int = 500
    include_file_dependencies: bool = True
    include_class_dependencies: bool = True
    include_method_dependencies: bool = False


class IncrementalRDFS:
    def __init__(
        self,
        repo_graph: DependencyGraph,
        options: DependencyExpansionOptions | None = None,
    ):
        self.repo_graph = repo_graph
        self.updated_files: set[str] = set()
        self.skipped_files: dict[str, str] = {}
        self.file_node_count_cache: dict[str, int] = {}
        self.options = options or DependencyExpansionOptions()
        self.graph_generator = TreeSitterDependencyGraphGenerator()

    def incremental_add_dependency(self, node: Any) -> bool:
        if not getattr(node, "location", None) or not node.location.file_path:
            return False
        file_path = str(node.location.file_path)
        if file_path in self.updated_files:
            return False
        if file_path in self.skipped_files:
            return False
        if self.options.max_files > 0 and len(self.updated_files) >= self.options.max_files:
            self.skipped_files[file_path] = f"dependency expansion budget exhausted ({self.options.max_files} files)"
            return False
        node_count = self._file_node_count(file_path)
        if self.options.max_nodes_per_file > 0 and node_count > self.options.max_nodes_per_file:
            self.skipped_files[file_path] = (
                f"file has {node_count} graph nodes, above limit {self.options.max_nodes_per_file}"
            )
            return False
        self.updated_files.add(file_path)
        self.repo_graph = self._incremental_update_graph_with_options(file_path)
        return True

    def stats(self) -> dict[str, Any]:
        return {
            "expanded_file_count": len(self.updated_files),
            "expanded_files": sorted(self.updated_files),
            "skipped_file_count": len(self.skipped_files),
            "skipped_files": dict(sorted(self.skipped_files.items())),
            "dependency_max_files": self.options.max_files,
            "dependency_max_nodes_per_file": self.options.max_nodes_per_file,
            "include_file_dependencies": self.options.include_file_dependencies,
            "include_class_dependencies": self.options.include_class_dependencies,
            "include_method_dependencies": self.options.include_method_dependencies,
        }

    def _file_node_count(self, file_path: str) -> int:
        if file_path not in self.file_node_count_cache:
            self.file_node_count_cache[file_path] = sum(
                1
                for node in self.repo_graph.graph.nodes
                if getattr(node, "location", None)
                and node.location.file_path
                and str(node.location.file_path) == file_path
            )
        return self.file_node_count_cache[file_path]

    def _incremental_update_graph_with_options(self, file_path: str) -> DependencyGraph:
        repo = Repository(self.repo_graph.repo_path, self.repo_graph.language)
        changed_files = [file_path]
        graph = self.repo_graph
        if self.options.include_file_dependencies:
            graph = self.graph_generator.generate_file_level_dependencies(graph, repo, changed_files=changed_files)
        if self.options.include_class_dependencies:
            graph = TreeSitterDependencyGraphGenerator.generate_class_level_dependencies(
                graph,
                repo,
                changed_files=changed_files,
            )
        if self.options.include_method_dependencies:
            graph = TreeSitterDependencyGraphGenerator.generate_method_level_dependencies(
                graph,
                repo,
                changed_files=changed_files,
            )
        return graph

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


def load_or_build_gcc_graph(cfg: RuntimeConfig, *, bug_id: str, checkout_commit: str) -> GraphBuildResult:
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

    gcc_root = cfg.paths.gcc_repo_dir / "gcc"
    if not gcc_root.exists():
        raise FileNotFoundError(f"gcc source directory not found: {gcc_root}")

    # GraphLocator builds the RDFS membership skeleton up front and lazily
    # adds expensive dependency edges when agents traverse selected files.
    repo = Repository(gcc_root, Language.CPP)
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
