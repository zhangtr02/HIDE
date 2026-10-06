from pathlib import Path

from rdfs.dependency_graph.dependency_graph import DependencyGraph
from rdfs.dependency_graph.graph_generator import GraphGeneratorType
from rdfs.dependency_graph.graph_generator.tree_sitter_generator import TreeSitterDependencyGraphGenerator
from rdfs.dependency_graph.models.language import Language
from rdfs.dependency_graph.models.repository import Repository


def construct_dependency_graph(repo, dependency_graph_generator: GraphGeneratorType, language: Language = None) -> DependencyGraph:
    if isinstance(repo, (str, Path)):
        if language is None:
            raise ValueError("language is required when repo is a path")
        repo = Repository(repo, language)

    if dependency_graph_generator != GraphGeneratorType.TREE_SITTER:
        raise ValueError("graphlocator_rustc only keeps the Tree-sitter graph generator")
    return TreeSitterDependencyGraphGenerator().generate(repo)


__all__ = [
    "DependencyGraph",
    "GraphGeneratorType",
    "Language",
    "Repository",
    "TreeSitterDependencyGraphGenerator",
    "construct_dependency_graph",
]
