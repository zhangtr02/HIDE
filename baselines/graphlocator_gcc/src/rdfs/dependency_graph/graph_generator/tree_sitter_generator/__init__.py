import copy
import os
from collections import defaultdict, deque
from pathlib import Path
from tree_sitter import Parser, Language as TS_Language, Node as TS_Node, Tree
from tqdm import tqdm
from textwrap import dedent
from rdfs.dependency_graph.dependency_graph import DependencyGraph
from rdfs.dependency_graph.graph_generator import BaseDependencyGraphGenerator
from rdfs.dependency_graph.graph_generator.tree_sitter_generator.finder import *
from rdfs.dependency_graph.graph_generator.tree_sitter_generator.resolve_import import (
    ImportResolver,
)
from rdfs.dependency_graph.graph_generator.tree_sitter_generator.load_lib import (
    get_builtin_lib_path,
)
from rdfs.dependency_graph.models import PathLike
from rdfs.dependency_graph.models.graph_data import (
    Node,
    NodeType,
    Location,
    EdgeRelation,
    Edge,
)
from rdfs.dependency_graph.models.language import Language
from rdfs.dependency_graph.models.repository import Repository
from rdfs.dependency_graph.utils.log import setup_logger
from rdfs.dependency_graph.utils.read_file import read_file_to_string
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from functools import partial
import shutil
import filecmp
import difflib
import tempfile
try:
    from utils import string_processing
except Exception:
    from utils import string_processing

# Initialize logging
logger = setup_logger()


class TreeSitterDependencyGraphGenerator(BaseDependencyGraphGenerator):

    supported_languages: tuple[Language] = (
        Language.Python,
        Language.Java,
        Language.CSharp,
        Language.TypeScript,
        Language.JavaScript,
        Language.Kotlin,
        Language.PHP,
        Language.Ruby,
        Language.C,
        Language.CPP,
        Language.Go,
        Language.Swift,
        Language.Rust,
        Language.Lua,
        Language.Bash,
        Language.R,
    )

    def __init__(self):
        self.import_map = None
        self.module_map = None

    def generate_file(
            self,
            repo: Repository,
            code: str = None,
            file_path: PathLike = None,
    ) -> DependencyGraph:
        raise NotImplementedError("generate_file is not implemented")

    @staticmethod
    def generate_file_node(graph: DependencyGraph, repo: Repository, root_node, suffixes):
        directories = set()
        for file in repo.files:
            path_tuple = file.file_path.relative_to(repo.repo_path).parts
            for index in range(len(path_tuple)):
                directories.add(os.path.join(str(repo.repo_path), '/'.join(path_tuple[:index + 1])))

        def dfs_directory_traversal(dir_path: str, parent_node: Node):

            while os.path.isdir(dir_path):
                sub_file_list = [f for f in os.listdir(dir_path) if not f.startswith('.')]
                available_path_num = 0
                available_path = ""
                for sub_file in sub_file_list:
                    path = os.path.join(dir_path, sub_file)
                    if path in directories:
                        available_path_num += 1
                        avaliable_path = sub_file
                if available_path_num == 1:
                    if os.path.isdir(os.path.join(dir_path, avaliable_path)):
                        dir_path += f"/{avaliable_path}"
                    else:
                        break
                else:
                    break

            name = dir_path.removeprefix(str(parent_node.location.file_path) + "/")
            if any([name.endswith(suffix) for suffix in suffixes]):
                child_node_type = NodeType.FILE
            else:
                child_node_type = NodeType.PACKAGE

            child_node = Node(type=child_node_type,
                              name=name.strip(),
                              location=Location(file_path=os.path.join(dir_path),
                                                start_line=None, start_column=None,
                                                end_line=None, end_column=None, ),
                              content=name.strip()
                              )
            if not graph.graph.has_node(child_node):
                graph.add_node(child_node)
            graph.add_relational_edge(
                parent_node,
                child_node,
                Edge(
                    relation=EdgeRelation.HasMember,
                    location=None,
                ),
            )

            if os.path.isdir(dir_path):
                files = os.listdir(dir_path)
                for f in files:
                    path = os.path.join(dir_path, f)
                    if path in directories:
                        dfs_directory_traversal(path, child_node)

        for f in os.listdir(str(root_node.location.file_path)):
            full_path = os.path.join(str(root_node.location.file_path), f)
            if full_path in directories:
                dfs_directory_traversal(full_path, root_node)
        return graph

    def generate(self, repo: Repository) -> DependencyGraph:
        D = DependencyGraph(repo.repo_path, repo.language)
        graph = TreeSitterDependencyGraphGenerator.generate_membership_hierarchical(D, repo)
        # graph = TreeSitterDependencyGraphGenerator.generate_file_level_dependencies(graph, repo)
        # graph = TreeSitterDependencyGraphGenerator.generate_class_level_dependencies(graph, repo)
        # graph = TreeSitterDependencyGraphGenerator.generate_method_level_dependencies(graph, repo)
        return graph

    @staticmethod
    def generate_single_file_membership_hierarchical_class(graph: DependencyGraph, repo: Repository, file_node: Node):
        new_edges = []
        try:
            code_context = file_node.get_text()
        except Exception as e:
            logger.error(f"Error reading file {file_node.location.file_path}: {e}")
            return []
        if len(code_context.strip()) == 0:
            return
        file_finder = FileFinder(repo.language, code_context)
        tree = file_finder.get_tree()

        class_nodes = file_finder.query_and_captures(FIND_CLASS_QUERY[repo.language], tree.root_node)
        noninternal_class_nodes = []
        for class_node in class_nodes:
            parent = class_node.parent
            if parent:
                parent = parent.parent
                if not (parent and parent.type in ['class_declaration', 'class_definition']):
                    noninternal_class_nodes.append(class_node)
            else:
                noninternal_class_nodes.append(class_node)
        new_class_nodes = []
        for class_node in noninternal_class_nodes:
            if class_node.type in ['class_declaration', 'class_definition']:
                node_type = NodeType.CLASS
            elif class_node.type == 'interface_declaration':
                node_type = NodeType.INTERFACE
            else:
                node_type = NodeType.ENUM

            extends_list = []
            implements_list = []
            use_list = []
            attribute_nodes = file_finder.query_and_captures(FIND_CLASS_ATTRIBUTE_QUERY[repo.language],
                                                             class_node,
                                                             capture_name=['extends', 'implements'])
            for attribute_node, attribute_type in attribute_nodes:
                attribute_location = Location(file_path=file_node.location.file_path,
                                              start_line=attribute_node.start_point[0] + 1,
                                              start_column=attribute_node.start_point[1] + 1,
                                              end_line=attribute_node.end_point[0] + 1,
                                              end_column=attribute_node.end_point[1] + 2)
                attribute_name = attribute_location.get_text()
                if attribute_type == 'extends':
                    extends_list.append(attribute_name.strip())
                elif attribute_type == 'implements':
                    implements_list.append(attribute_name.strip())
            use_nodes = file_finder.query_and_captures(FIND_CLASS_USE_QUERY[repo.language],
                                                       class_node.child_by_field_name('body'))
            for use_node in use_nodes:
                use_name = use_node.text.decode('utf-8')
                use_list.append(use_name.strip())

            name_location = Location(file_path=file_node.location.file_path,
                                     start_line=class_node.child_by_field_name('name').start_point[0] + 1,
                                     start_column=class_node.child_by_field_name('name').start_point[1] + 1,
                                     end_line=class_node.child_by_field_name('name').end_point[0] + 1,
                                     end_column=class_node.child_by_field_name('name').end_point[1] + 2)
            body_start_pos = class_node.child_by_field_name('body').start_point
            if body_start_pos[1] == 0:
                signature_end_line = body_start_pos[0]
                signature_end_column = body_start_pos[1]
            else:
                signature_end_line = body_start_pos[0]
                signature_end_column = body_start_pos[1] - 1
            class_location = Location(file_path=file_node.location.file_path,
                                      start_line=class_node.start_point[0] + 1,
                                      start_column=0,
                                      end_line=signature_end_line + 1,
                                      end_column=signature_end_column + 1)
            extends_list.sort()
            implements_list.sort()
            use_list = list(set(use_list))
            use_list.sort()
            class_name = name_location.get_text().strip().strip(':()[]{}.<>')
            new_class_node = Node(
                type=node_type,
                name=class_name,
                location=Location(file_path=file_node.location.file_path,
                                      start_line=class_node.start_point[0] + 1,
                                      start_column=0,
                                      end_line=class_node.end_point[0] + 1,
                                      end_column=class_node.end_point[1] + 1),
                attribute={
                    "extends": extends_list.copy(),
                    "implements": implements_list.copy(),
                    "uses": use_list.copy()
                },
                content=class_location.get_text().rstrip("{").strip(),
            )
            if not graph.graph.has_node(new_class_node):
                graph.add_node(new_class_node)
                new_class_nodes.append(new_class_node)
            new_edges.append((file_node,
                new_class_node,
                Edge(
                    relation=EdgeRelation.HasMember,
                    location=None,
                )))
            if repo.language == Language.Python:
                all_field_nodes = file_finder.query_and_captures(FIND_FIELD_QUERY[repo.language], class_node)
                field_nodes = [n for n in all_field_nodes if n.child_by_field_name("object").text.decode("utf8") == "self"]
            else:
                field_nodes = file_finder.query_and_captures(FIND_FIELD_QUERY[repo.language], class_node)
            for field_node in field_nodes:
                parent = field_node.parent
                if parent and parent.type == 'declaration_list':
                    parent = parent.parent
                    if parent and parent.type == 'class_declaration':
                        parent_name = parent.child_by_field_name('name').text.decode()
                        if parent_name != class_name:
                            continue
                if repo.language == Language.Python:
                    field_name = field_node
                    field_location = None
                    field_name_location = Location(file_path=file_node.location.file_path,
                                                   start_line=field_name.start_point[0] + 1,
                                                   start_column=field_name.start_point[1] + 1,
                                                   end_line=field_name.end_point[0] + 1,
                                                   end_column=field_name.end_point[1] + 2, )
                    content = field_name_location.get_text().strip().strip(':()[]{}.<>')
                else:
                    field_name = list(file_finder.query_and_captures(FIND_FIELD_NAME_QUERY[repo.language], field_node))[
                        0]
                    field_name_location = Location(file_path=file_node.location.file_path,
                                                   start_line=field_name.start_point[0] + 1,
                                                   start_column=field_name.start_point[1] + 1,
                                                   end_line=field_name.end_point[0] + 1,
                                                   end_column=field_name.end_point[1] + 2, )
                    field_location = Location(file_path=file_node.location.file_path,
                                              start_line=field_node.start_point[0] + 1,
                                              start_column=field_node.start_point[1] + 1,
                                              end_line=field_node.end_point[0] + 1,
                                              end_column=field_node.end_point[1] + 2, )
                    content = field_location.get_text().strip()
                if repo.language == Language.Python and field_name_location.get_text().strip().strip(':').endswith("("):
                        continue
                new_field_node = Node(
                    type=NodeType.FIELD,
                    name=field_name_location.get_text().strip().strip(':.[()]{}'),
                    location=field_location,
                    content=content
                )
                if not graph.graph.has_node(new_field_node):
                    graph.add_node(new_field_node)
                new_edges.append((
                    new_class_node,
                    new_field_node,
                    Edge(
                        relation=EdgeRelation.HasMember,
                        location=None,
                    )
                ))
            method_nodes = file_finder.query_and_captures(FIND_METHOD_QUERY[repo.language], class_node)
            for method_node in method_nodes:
                parent = method_node.parent
                if parent and parent.type == 'declaration_list':
                    parent = parent.parent
                    if parent and parent.type == 'class_declaration':
                        parent_name = parent.child_by_field_name('name').text.decode()
                        if parent_name != class_name:
                            continue
                if method_node.type in ["method_declaration", "method_definition"]:
                    node_type = NodeType.METHOD
                elif method_node.type in ["function_declaration", "function_definition"]:
                    node_type = NodeType.FUNCTION
                else:
                    node_type = NodeType.CONSTRUCTOR
                method_call_set = file_finder.query_and_captures(FIND_METHOD_CALL_QUERY[repo.language], method_node)
                method_call_list = []
                for method_call in method_call_set:
                    method_call_location = Location(file_path=file_node.location.file_path,
                                                    start_line=method_call.start_point[0] + 1,
                                                    start_column=method_call.start_point[1] + 1,
                                                    end_line=method_call.end_point[0] + 1,
                                                    end_column=method_call.end_point[1] + 2)
                    method_call_name = method_call.text.decode('utf-8')
                    method_call_list.append(method_call_name)
                method_call_list.sort()
                name_location = Location(file_path=file_node.location.file_path,
                                         start_line=method_node.child_by_field_name('name').start_point[0] + 1,
                                         start_column=method_node.child_by_field_name('name').start_point[1] + 1,
                                         end_line=method_node.child_by_field_name('name').end_point[0] + 1,
                                         end_column=method_node.child_by_field_name('name').end_point[1] + 1, )
                method_location = Location(file_path=file_node.location.file_path,
                                           start_line=method_node.start_point[0] + 1, start_column=0,
                                           end_line=method_node.end_point[0] + 1,
                                           end_column=method_node.end_point[1] + 2, )
                new_method_node = Node(
                    type=node_type,
                    name=name_location.get_text().strip(),
                    location=method_location,
                    attribute={'method_call_list': method_call_list.copy()},
                    content=dedent(method_location.get_text().strip('\n'))
                )
                if not graph.graph.has_node(new_method_node):
                    graph.add_node(new_method_node)
                new_edges.append((
                    new_class_node,
                    new_method_node,
                    Edge(
                        relation=EdgeRelation.HasMember,
                        location=None,
                    )
                ))

        for i, e_class_node in enumerate(noninternal_class_nodes):
            body_node = e_class_node.child_by_field_name("body")
            class_nodes = file_finder.query_and_captures(FIND_CLASS_QUERY[repo.language], body_node)
            for class_node in class_nodes:
                if class_node.type in ['class_declaration', 'class_definition']:
                    node_type = NodeType.CLASS
                elif class_node.type == 'interface_declaration':
                    node_type = NodeType.INTERFACE
                else:
                    node_type = NodeType.ENUM

                extends_list = []
                implements_list = []
                use_list = []
                attribute_nodes = file_finder.query_and_captures(FIND_CLASS_ATTRIBUTE_QUERY[repo.language],
                                                                 class_node,
                                                                 capture_name=['extends', 'implements'])
                for attribute_node, attribute_type in attribute_nodes:
                    attribute_location = Location(file_path=file_node.location.file_path,
                                                  start_line=attribute_node.start_point[0] + 1,
                                                  start_column=attribute_node.start_point[1] + 1,
                                                  end_line=attribute_node.end_point[0] + 1,
                                                  end_column=attribute_node.end_point[1] + 2)
                    attribute_name = attribute_location.get_text()
                    if attribute_type == 'extends':
                        extends_list.append(attribute_name.strip())
                    elif attribute_type == 'implements':
                        implements_list.append(attribute_name.strip())
                use_nodes = file_finder.query_and_captures(FIND_CLASS_USE_QUERY[repo.language],
                                                           class_node.child_by_field_name('body'))
                for use_node in use_nodes:
                    use_location = Location(file_path=file_node.location.file_path,
                                            start_line=use_node.start_point[0] + 1,
                                            start_column=use_node.start_point[1] + 1,
                                            end_line=use_node.end_point[0] + 1,
                                            end_column=use_node.end_point[1] + 2)
                    use_name = use_node.text.decode('utf-8')
                    use_list.append(use_name.strip())

                name_location = Location(file_path=file_node.location.file_path,
                                         start_line=class_node.child_by_field_name('name').start_point[0] + 1,
                                         start_column=class_node.child_by_field_name('name').start_point[1] + 1,
                                         end_line=class_node.child_by_field_name('name').end_point[0] + 1,
                                         end_column=class_node.child_by_field_name('name').end_point[1] + 2)
                body_start_pos = class_node.child_by_field_name('body').start_point
                if body_start_pos[1] == 0:
                    signature_end_line = body_start_pos[0]
                    signature_end_column = body_start_pos[1]
                else:
                    signature_end_line = body_start_pos[0]
                    signature_end_column = body_start_pos[1] - 1
                class_location = Location(file_path=file_node.location.file_path,
                                          start_line=class_node.start_point[0] + 1,
                                          start_column=0,  # 为typescript做的优化
                                          end_line=signature_end_line + 1,
                                          end_column=signature_end_column + 1)
                extends_list.sort()
                implements_list.sort()
                use_list = list(set(use_list))
                use_list.sort()
                class_name = name_location.get_text().strip().strip(':.[()]<>{}')
                new_class_node = Node(
                    type=node_type,
                    name=class_name,
                    location=Location(file_path=file_node.location.file_path,
                                          start_line=class_node.start_point[0] + 1,
                                          start_column=0,  
                                          end_line=class_node.end_point[0] + 1,
                                          end_column=class_node.end_point[1] + 2),
                    attribute={
                        "extends": extends_list.copy(),
                        "implements": implements_list.copy(),
                        "uses": use_list.copy()
                    },
                    content=class_location.get_text().rstrip("{").strip(),
                )
                if not graph.graph.has_node(new_class_node):
                    graph.add_node(new_class_node)
                new_edges.append((
                    new_class_nodes[i],
                    new_class_node,
                    Edge(
                        relation=EdgeRelation.HasMember,
                        location=None,
                    )
                ))
                if repo.language == Language.Python:
                    all_field_nodes = file_finder.query_and_captures(FIND_FIELD_QUERY[repo.language], class_node)
                    field_nodes = [n for n in all_field_nodes if
                                   n.child_by_field_name("object").text.decode("utf8") == "self"]
                else:
                    field_nodes = file_finder.query_and_captures(FIND_FIELD_QUERY[repo.language], class_node)
                for field_node in field_nodes:
                    if repo.language == Language.Python:
                        field_name = field_node
                        field_location = None
                        field_name_location = Location(file_path=file_node.location.file_path,
                                                       start_line=field_name.start_point[0] + 1,
                                                       start_column=field_name.start_point[1] + 1,
                                                       end_line=field_name.end_point[0] + 1,
                                                       end_column=field_name.end_point[1] + 2, )
                        content = field_name_location.get_text().strip().strip(':()[]{}.<>,')
                    else:
                        field_name = list(file_finder.query_and_captures(FIND_FIELD_NAME_QUERY[repo.language], field_node))[
                            0]
                        field_name_location = Location(file_path=file_node.location.file_path,
                                                       start_line=field_name.start_point[0] + 1,
                                                       start_column=field_name.start_point[1] + 1,
                                                       end_line=field_name.end_point[0] + 1,
                                                       end_column=field_name.end_point[1] + 2, )
                        field_location = Location(file_path=file_node.location.file_path,
                                                  start_line=field_node.start_point[0] + 1,
                                                  start_column=field_node.start_point[1] + 1,
                                                  end_line=field_node.end_point[0] + 1,
                                                  end_column=field_node.end_point[1] + 2, )
                        content = field_location.get_text().strip()

                    new_field_node = Node(
                        type=NodeType.FIELD,
                        name=field_name_location.get_text().strip().strip(':()[]{}.<>,'),
                        location=field_location,
                        content=content
                    )
                    if not graph.graph.has_node(new_field_node):
                        graph.add_node(new_field_node)
                    new_edges.append((
                        new_class_node,
                        new_field_node,
                        Edge(
                            relation=EdgeRelation.HasMember,
                            location=None,
                        )
                    ))
                method_nodes = file_finder.query_and_captures(FIND_METHOD_QUERY[repo.language], class_node)
                for method_node in method_nodes:
                    if method_node.type in ["method_declaration", "method_definition"]:
                        node_type = NodeType.METHOD
                    elif method_node.type == ["function_declaration", "function_definition"]:
                        node_type = NodeType.FUNCTION
                    else:
                        node_type = NodeType.CONSTRUCTOR
                    method_call_set = file_finder.query_and_captures(FIND_METHOD_CALL_QUERY[repo.language], method_node)
                    method_call_list = []
                    for method_call in method_call_set:
                        method_call_location = Location(file_path=file_node.location.file_path,
                                                        start_line=method_call.start_point[0] + 1,
                                                        start_column=method_call.start_point[1] + 1,
                                                        end_line=method_call.end_point[0] + 1,
                                                        end_column=method_call.end_point[1] + 2)
                        method_call_name = method_call.text.decode('utf-8')
                        method_call_list.append(method_call_name)
                    method_call_list.sort()
                    name_location = Location(file_path=file_node.location.file_path,
                                             start_line=method_node.child_by_field_name('name').start_point[0] + 1,
                                             start_column=method_node.child_by_field_name('name').start_point[1] + 1,
                                             end_line=method_node.child_by_field_name('name').end_point[0] + 1,
                                             end_column=method_node.child_by_field_name('name').end_point[1] + 1, )
                    method_location = Location(file_path=file_node.location.file_path,
                                               start_line=method_node.start_point[0] + 1, start_column=0,
                                               end_line=method_node.end_point[0] + 1,
                                               end_column=method_node.end_point[1] + 2, )
                    new_method_node = Node(
                        type=node_type,
                        name=name_location.get_text().strip(),
                        location=method_location,
                        attribute={'method_call_list': method_call_list.copy()},
                        content=dedent(method_location.get_text().strip('\n'))
                    )
                    if not graph.graph.has_node(new_method_node):
                        graph.add_node(new_method_node)
                    new_edges.append((
                        new_class_node,
                        new_method_node,
                        Edge(
                            relation=EdgeRelation.HasMember,
                            location=None,
                        )
                    ))
        if repo.language == Language.Python:
            # Add function node
            function_nodes = file_finder.query_and_captures(FIND_FUNCTION_QUERY[repo.language], tree.root_node)
            for function_node in function_nodes:
                node_type = NodeType.FUNCTION
                method_call_set = set()
                function_location = Location(file_path=file_node.location.file_path,
                                             start_line=function_node.start_point[0] + 1,
                                             start_column=0,
                                             end_line=function_node.end_point[0] + 1,
                                             end_column=function_node.end_point[1] + 2)
                function_node_name = function_node.child_by_field_name("name").text.decode()
                body_node = function_node.child_by_field_name("body")
                identifier_nodes = file_finder.query_and_captures(FIND_IDENTIFIER_QUERY[repo.language], body_node)
                for ind in identifier_nodes:
                    method_call_set.add(ind.text.decode())
                new_function_node = Node(
                    type=node_type,
                    name=function_node_name,
                    location=function_location,
                    attribute={'method_call_list': list(method_call_set).copy()},
                    content=function_location.get_text().strip(),
                )
                if not graph.graph.has_node(new_function_node):
                    graph.add_node(new_function_node)
                new_edges.append((file_node,
                                  new_function_node,
                                  Edge(
                                      relation=EdgeRelation.HasMember,
                                      location=None,
                                  )))
            # Add global_var
            nodes = file_finder.query_and_captures(FIND_GLOBAL_VAR_QUERY[repo.language], tree.root_node)
            for node in nodes:
                node_type = NodeType.GLOBAL_VAR

                location = Location(file_path=file_node.location.file_path,
                                    start_line=node.start_point[0] + 1,
                                    start_column=0,
                                    end_line=node.end_point[0] + 1,
                                    end_column=node.end_point[1] + 2)
                node_name = node.child_by_field_name("left").text.decode()
                new_node = Node(
                    type=node_type,
                    name=node_name,
                    location=location,
                    attribute={},
                    content=location.get_text().strip(),
                )
                new_class_node = Node(
                    type=NodeType.CLASS,
                    name=node_name,
                    location=location,
                    attribute={"uses": []},
                    content="",
                )
                if not graph.graph.has_node(new_node):
                    graph.add_node(new_node)
                    graph.add_node(new_class_node)
                new_edges.append((file_node,
                                  new_class_node,
                                  Edge(
                                      relation=EdgeRelation.HasMember,
                                      location=None,
                                  )))
                new_edges.append((new_class_node,
                                  new_node,
                                  Edge(
                                      relation=EdgeRelation.HasMember,
                                      location=None,
                                  )))

        return new_edges

    @staticmethod
    def generate_single_file_membership_hierarchical_struct(graph: DependencyGraph, repo: Repository, file_node: Node):
        new_edges = []
        code_context = file_node.get_text()
        if len(code_context.strip()) == 0:
            return

        file_finder = FileFinder(repo.language, code_context)
        tree = file_finder.get_tree()

        # Add structure/enum node
        struct_nodes = file_finder.query_and_captures(FIND_CLASS_QUERY[repo.language], tree.root_node)
        for struct_node in struct_nodes:
            use_list = []
            if struct_node.type in {"class_specifier", "struct_specifier"} or "struct" in struct_node.text.decode():
                node_type = NodeType.STRUCTURE
            else:
                node_type = NodeType.ENUM

            struct_location = Location(file_path=file_node.location.file_path,
                                      start_line=struct_node.start_point[0] + 1,
                                      start_column=0,
                                      end_line=struct_node.end_point[0] + 1,
                                      end_column=struct_node.end_point[1] + 2)
            if struct_node.child_by_field_name("name"):
                struct_node_name = struct_node.child_by_field_name("name").text.decode()
                body_node = struct_node.child_by_field_name("body")
            else:
                struct_node_name = struct_node.child_by_field_name("declarator").text.decode()
                body_node = struct_node.child_by_field_name("type").child_by_field_name("body")
            if body_node:
                all_type_identifier_nodes = file_finder.query_and_captures(FIND_TYPE_IDENTIFIER_QUERY[repo.language], body_node)
                for tid in all_type_identifier_nodes:
                    use_list.append(tid.text.decode('utf-8'))

                new_struct_node = Node(
                    type=node_type,
                    name=struct_node_name,
                    location=struct_location,
                    attribute={"uses": use_list.copy()},
                    content=struct_location.get_text().strip(),
                )
                if not graph.graph.has_node(new_struct_node):
                    graph.add_node(new_struct_node)
                new_edges.append((file_node,
                                  new_struct_node,
                                  Edge(
                                    relation=EdgeRelation.HasMember,
                                    location=None,
                                )))

        # Add structure implement
        # if repo.language == Language.Rust:
        #     impl_blocks = file_finder.query_and_captures(FIND_IMPL_BLOCK[repo.language], tree.root_node)
        #     for impl_block in impl_blocks:
        #         associated_struct = impl_block.child_by_field_name("type").text.decode()
        #         struct_nodes = graph.get_nodes(node_filter=lambda x: x.name==associated_struct and x.type==NodeType.STRUCTURE)
        #         if len(struct_nodes) == 0:
        #             continue
        #
        #         struct_node = struct_nodes[0]
        #         function_nodes = file_finder.query_and_captures(FIND_METHOD_QUERY[repo.language], impl_block)
        #         for function_node in function_nodes:
        #             node_type = NodeType.FUNCTION
        #             method_call_set = set()
        #             function_location = Location(file_path=file_node.location.file_path,
        #                                          start_line=function_node.start_point[0] + 1,
        #                                          start_column=0,
        #                                          end_line=function_node.end_point[0] + 1,
        #                                          end_column=function_node.end_point[1] + 2)
        #             if function_node.child_by_field_name("declarator"):
        #                 dc = function_node.child_by_field_name("declarator")
        #                 while dc.child_by_field_name("declarator"):
        #                     dc = dc.child_by_field_name("declarator")
        #                 function_node_name = dc.text.decode()
        #             else:
        #                 function_node_name = function_node.child_by_field_name("name").text.decode()
        #             body_node = function_node.child_by_field_name("body")
        #             identifier_nodes = file_finder.query_and_captures(FIND_IDENTIFIER_QUERY[repo.language], body_node)
        #             for ind in identifier_nodes:
        #                 method_call_set.add(ind.text.decode())
        #             identifier_nodes = file_finder.query_and_captures(FIND_TYPE_IDENTIFIER_QUERY[repo.language],
        #                                                               body_node)
        #             for ind in identifier_nodes:
        #                 method_call_set.add(ind.text.decode())
        #                 struct_node.attribute['uses'].append(ind.text.decode())
        #             new_function_node = Node(
        #                 type=node_type,
        #                 name=function_node_name,
        #                 location=function_location,
        #                 attribute={'method_call_list': list(method_call_set).copy()},
        #                 content=function_location.get_text().strip(),
        #             )
        #
        #             if not graph.graph.has_node(new_function_node):
        #                 graph.add_node(new_function_node)
        #             new_edges.append((struct_node,
        #                               new_function_node,
        #                               Edge(
        #                                   relation=EdgeRelation.HasMember,
        #                                   location=None,
        #                               )))
        # Add function node
        function_nodes = file_finder.query_and_captures(FIND_FUNCTION_QUERY[repo.language], tree.root_node)
        for function_node in function_nodes:
            node_type = NodeType.FUNCTION
            method_call_set = set()
            function_location = Location(file_path=file_node.location.file_path,
                                       start_line=function_node.start_point[0] + 1,
                                       start_column=0,
                                       end_line=function_node.end_point[0] + 1,
                                       end_column=function_node.end_point[1] + 2)
            if function_node.child_by_field_name("declarator"):
                dc = function_node.child_by_field_name("declarator")
                while dc.child_by_field_name("declarator"):
                    dc = dc.child_by_field_name("declarator")
                function_node_name = dc.text.decode()
            else:
                function_node_name = function_node.child_by_field_name("name").text.decode()
            body_node = function_node.child_by_field_name("body")
            identifier_nodes = file_finder.query_and_captures(FIND_IDENTIFIER_QUERY[repo.language], body_node)
            for ind in identifier_nodes:
                method_call_set.add(ind.text.decode())
            identifier_nodes = file_finder.query_and_captures(FIND_TYPE_IDENTIFIER_QUERY[repo.language], body_node)
            for ind in identifier_nodes:
                method_call_set.add(ind.text.decode())
            new_class_node = Node(
                type=NodeType.CLASS,
                name=function_node_name,
                location=function_location,
                attribute={"uses": list(method_call_set).copy()},
                content="",
            )
            new_function_node = Node(
                type=node_type,
                name=function_node_name,
                location=function_location,
                attribute={'method_call_list': list(method_call_set).copy()},
                content=function_location.get_text().strip(),
            )
            if not graph.graph.has_node(new_function_node):
                graph.add_node(new_function_node)
                graph.add_node(new_class_node)
            new_edges.append((file_node,
                              new_class_node,
                              Edge(
                                  relation=EdgeRelation.HasMember,
                                  location=None,
                              )))
            new_edges.append((new_class_node,
                              new_function_node,
                              Edge(
                                  relation=EdgeRelation.HasMember,
                                  location=None,
                              )))
        # Add preproc_def
        if repo.language in [Language.C, Language.CPP]:
            nodes = file_finder.query_and_captures(FIND_PREPROC_DEF_QUERY[repo.language], tree.root_node)
            for node in nodes:
                node_type = NodeType.PREPROC_DEF

                location = Location(file_path=file_node.location.file_path,
                                    start_line=node.start_point[0] + 1,
                                    start_column=0,
                                    end_line=node.end_point[0] + 1,
                                    end_column=node.end_point[1] + 2)
                node_name = node.child_by_field_name("name").text.decode()
                new_node = Node(
                    type=node_type,
                    name=node_name,
                    location=location,
                    attribute={},
                    content=location.get_text().strip(),
                )
                if not graph.graph.has_node(new_node):
                    graph.add_node(new_node)
                new_edges.append((file_node,
                                  new_node,
                                  Edge(
                                      relation=EdgeRelation.HasMember,
                                      location=None,
                                  )))

        # Add global_var
        nodes = file_finder.query_and_captures(FIND_GLOBAL_VAR_QUERY[repo.language], tree.root_node)
        for node in nodes:
            node_type = NodeType.GLOBAL_VAR

            location = Location(file_path=file_node.location.file_path,
                                start_line=node.start_point[0] + 1,
                                start_column=0,
                                end_line=node.end_point[0] + 1,
                                end_column=node.end_point[1] + 2)
            node_names = list(file_finder.query_and_captures(FIND_IDENTIFIER_QUERY[repo.language], node))
            node_name = node_names[0].text.decode()
            new_node = Node(
                type=node_type,
                name=node_name,
                location=location,
                attribute={},
                content=location.get_text().strip(),
            )
            new_class_node = Node(
                type=NodeType.CLASS,
                name=node_name,
                location=location,
                attribute={},
                content="",
            )
            if not graph.graph.has_node(new_node):
                graph.add_node(new_node)
            new_edges.append((file_node,
                              new_class_node,
                              Edge(
                                  relation=EdgeRelation.HasMember,
                                  location=None,
                              )))

            new_edges.append((new_class_node,
                              new_node,
                              Edge(
                                  relation=EdgeRelation.HasMember,
                                  location=None,
                              )))
        return new_edges

    @staticmethod
    def generate_membership_hierarchical(graph: DependencyGraph, repo: Repository, changed_files=[]):
        """
        Generate the membership hierarchical graph.等价于文件结构树和文件内部的展开
        :param changed_files:
        :param graph:
        :param repo:
        :return:
        """
        repo_name = repo.repo_path.name
        suffixes = repo.code_file_extensions[repo.language]
        if len(changed_files) == 0:
            root_node = Node(type=NodeType.REPO,
                             name=repo_name.strip(),
                             location=Location(file_path=repo.repo_path, start_line=None, start_column=None,
                                               end_line=None, end_column=None, ),
                             content=repo_name.strip()
                             )

            graph.add_node(root_node)
        else:
            root_node = graph.get_nodes(node_filter=lambda x: x.type == NodeType.REPO)[0]
        graph = TreeSitterDependencyGraphGenerator.generate_file_node(graph, repo, root_node, suffixes)
        if len(changed_files) == 0:
            all_node_list = graph.get_nodes(node_filter=lambda node: node.type == NodeType.FILE)
        else:
            all_node_list = graph.get_nodes(node_filter=lambda node: node.type == NodeType.FILE and str(node.location.file_path) in changed_files)
        max_processes = max(1, (os.cpu_count() or 1) // 5)
        if repo.language in [Language.C, Language.CPP, Language.Rust]:
            worker_partial = partial(TreeSitterDependencyGraphGenerator.generate_single_file_membership_hierarchical_struct,
                                     graph, repo)
        else:
            worker_partial = partial(TreeSitterDependencyGraphGenerator.generate_single_file_membership_hierarchical_class,
                                     graph, repo)
        

        with ProcessPoolExecutor(max_processes) as executor:
            all_edges = list(tqdm(executor.map(worker_partial, all_node_list), total=len(all_node_list),
                                  desc="Generating membership hierarchical graph"))
            for edges in all_edges:
                if edges:
                    graph.add_relational_edges_from(edges)
        return graph

    @staticmethod
    def is_third_party_import(package_name, api_name, module_map, current_file_path, language):
        match language:
            case Language.Java:
                key_name = f"{package_name}.{api_name}"
                if key_name not in module_map.keys():
                    return True
                importee_file_path = module_map[key_name][0]
                if (not importee_file_path.exists() or not importee_file_path.is_file()):
                    return True
                else:
                    return False
            case Language.TypeScript:
                current_dir = os.path.dirname(current_file_path)
                importee_file_path = os.path.abspath(os.path.join(current_dir, f"{package_name}.ets"))
                exist = os.path.exists(importee_file_path)
                if exist:
                    return False
                else:
                    return True
            case Language.CSharp:
                key_name = package_name
                if key_name not in module_map.keys():
                    return True
                else:
                    return False
            case _:
                raise NotImplementedError

    @staticmethod
    def find_import_node(graph, package_name, api_name, language, module_map, current_file_path):
        match language:
            case Language.Java:
                key_name = f"{package_name}.{api_name}"
                if key_name not in module_map.keys():
                    return []
                importee_file_path = module_map[key_name][0]
                node_found = graph.get_nodes(
                    node_filter=lambda x: (x.location and x.location and str(x.location.file_path) == str(importee_file_path))
                                          and x.name.strip() == api_name.strip() + ".java"
                )
                if len(node_found) > 0:
                    return node_found
                else:
                    return []
            case Language.TypeScript:
                current_dir = os.path.dirname(current_file_path)
                importee_file_path = os.path.abspath(os.path.join(current_dir, f"{package_name}.ets"))
                node_found = graph.get_nodes(
                    node_filter=lambda x: x.location and (str(x.location.file_path) == importee_file_path) and x.type==NodeType.FILE
                )
                if len(node_found) > 0:
                    return node_found
                else:
                    return []
            case Language.CSharp:
                key_name = f"{package_name}"
                if key_name not in module_map.keys():
                    return []
                importee_file_path = [str(f) for f in module_map[key_name]]
                node_found = graph.get_nodes(
                    node_filter=lambda x: (x.location and x.location and str(x.location.file_path) in importee_file_path
                                           and x.type == NodeType.FILE)
                )
                if len(node_found) > 0:
                    return node_found
                else:
                    return []
            case Language.Python:
                key_name = f"{api_name}"
                if key_name not in module_map.keys():
                    return []
                if len(module_map[key_name]) > 0:
                    return module_map[key_name]
                else:
                    return []
            case Language.C | Language.CPP:
                key_name = f"{api_name}"
                if key_name not in module_map.keys():
                    return []
                if len(module_map[key_name]) > 0:
                    return module_map[key_name]
                else:
                    return []
            case Language.Rust:
                key_name = f"{api_name}"
                if key_name not in module_map.keys():
                    return []
                if len(module_map[key_name]) > 0:
                    return module_map[key_name]
                else:
                    return []
            case _:
                raise NotImplementedError


    @staticmethod
    def generate_single_file_package(graph, package_map, name):
        new_edges = []
        paths = package_map[name]
        package_node = Node(type=NodeType.PACKAGE,
                            name=name.strip(),
                            location=None,
                            content=name.strip())
        if not graph.graph.has_node(package_node):
            graph.add_node(package_node)
        for path in paths:
            new_edges.append((package_node,
                              path,
                              Edge(relation=EdgeRelation.HasMember,
                                   location=None)
                            ))
        return new_edges

    def resolving_imports(self, repo, graph, current_file_node):
        new_edges = []
        import_symbol_nodes = self.import_map[current_file_node]
        for importee_node in import_symbol_nodes:
            package_name, api_name_list = importee_node[0], importee_node[1]
            for api_name in api_name_list:
                node_list = TreeSitterDependencyGraphGenerator.find_import_node(graph, package_name,
                                                                                api_name, repo.language, self.module_map,
                                                                                str(current_file_node.location.file_path))
                for v in node_list:
                    if not graph.graph.has_edge(v, current_file_node):
                        new_edges.append(((v, current_file_node,
                                                  Edge(
                                                      relation=EdgeRelation.ImportedBy,
                                                      location=None,
                                                  )
                                                  )))
        return new_edges

    def incremental_resolving_imports(self, repo, graph, current_file_node):
        new_edges = []
        # 正向
        import_symbol_nodes = self.import_map[current_file_node]
        for importee_node in import_symbol_nodes:
            package_name, api_name_list = importee_node[0], importee_node[1]
            for api_name in api_name_list:
                if api_name in self.module_map.keys():
                    if package_name.startswith("."):
                        file_path = string_processing.calculate_import_path(current_file_node.location.file_path, package_name)
                        node_list = graph.get_nodes(node_filter=lambda x: x.type == NodeType.FILE and x.location.file_path == file_path)
                    else:
                        node_list = TreeSitterDependencyGraphGenerator.find_import_node(graph, package_name,
                                                                api_name, repo.language,
                                                                self.module_map,
                                                                str(current_file_node.location.file_path))
                elif package_name:
                    file_path = package_name.replace(".", "/")+".py"
                    node_list = graph.get_nodes(node_filter=lambda x: x.type == NodeType.FILE and file_path in str(x.location.file_path))
                else:
                    node_list = TreeSitterDependencyGraphGenerator.find_import_node(graph, package_name,
                                                                                    api_name, repo.language, self.module_map,
                                                                                    str(current_file_node.location.file_path))
                for v in node_list:
                    if not graph.graph.has_edge(v, current_file_node):
                        new_edges.append(((v, current_file_node,
                                                  Edge(
                                                      relation=EdgeRelation.ImportedBy,
                                                      location=None,
                                                  )
                                                  )))
        # 反向
        current_module_name = ""
        for k, v in self.module_map.items():
            if current_file_node in v:
                current_module_name = k
                break
        if current_module_name:
            for k, import_symbol_nodes in self.import_map.items():
                for importee_node in import_symbol_nodes:
                    package_name, api_name_list = importee_node[0], importee_node[1]
                    if current_module_name in api_name_list:
                        if package_name.startswith("."):
                            file_path = string_processing.calculate_import_path(k.location.file_path, package_name)
                            if file_path == current_file_node.location.file_path:
                                new_edges.append(((current_file_node, k,
                                                          Edge(
                                                              relation=EdgeRelation.ImportedBy,
                                                              location=None,
                                                          )
                                                          )))
                    elif package_name != "":
                        file_path = package_name.replace(".", "/") + ".py"
                        if file_path in str(current_file_node.location.file_path):
                            new_edges.append(((current_file_node, k,
                                                      Edge(
                                                          relation=EdgeRelation.ImportedBy,
                                                          location=None,
                                                      )
                                                      )))
        return new_edges

    def generate_file_level_dependencies(self, graph: DependencyGraph, repo: Repository, changed_files=[]):

        if self.import_map is None or self.module_map is None:
            self.module_map: dict[str, list[Node]] = defaultdict(list)
            self.import_map: dict[Node, list[TS_Node]] = defaultdict(list)
            finder = ImportFinder(repo.language)
            all_file_nodes = graph.get_nodes(node_filter=lambda x: x.type == NodeType.FILE)
            for file_node in tqdm(all_file_nodes, desc="Generating graph"):
                content = file_node.location.get_text()
                if not content.strip():
                    continue
                if name := finder.find_module_name(content, Path(file_node.location.file_path)):
                    self.module_map[name].append(file_node)
                nodes = finder.find_imports(content)
                self.import_map[file_node].extend(nodes)

        max_processes = max(1, (os.cpu_count() or 1)//5)
        # if repo.language in [Language.Java, Language.CSharp]:
        #     items = list(self.module_map.keys())
        #     worker_partial = partial(TreeSitterDependencyGraphGenerator.generate_single_file_package,
        #                              graph, self.module_map)
        #     with ProcessPoolExecutor(max_processes) as executor:
        #         all_edges = list(tqdm(executor.map(worker_partial, items), total=len(items),
        #                           desc="Generating file level dependencies"))
        #     for edges in all_edges:
        #         if edges:
        #             graph.add_relational_edges_from(edges)
        if len(changed_files) == 0:
            api_items = list(self.import_map.keys())
            worker_partial = partial(self.resolving_imports, repo,
                                     graph)
        else:
            api_items = [v for v in self.import_map.keys() if str(v.location.file_path) in changed_files]
            worker_partial = partial(self.incremental_resolving_imports, repo,
                                     graph)
        with ProcessPoolExecutor(max_processes) as executor:
            all_edges = list(tqdm(executor.map(worker_partial, api_items), total=len(api_items), desc="Resolving imports"))
            for edges in all_edges:
                if edges:
                    graph.add_relational_edges_from(edges)
        return graph

    @staticmethod
    def generate_single_class(graph, all_class_name, class_node):
        new_edges = []
        file_node = graph.get_membership_parent(class_node)
        if 'extends' in class_node.attribute.keys():
            for extend_name in class_node.attribute['extends']:
                candidate_nodes = graph.get_nodes(
                    node_filter=lambda node: node.type == NodeType.CLASS and node.name == extend_name)
                if len(candidate_nodes) == 0:
                    continue
                for candidate_node in candidate_nodes:
                    candidate_node_file = graph.get_membership_parent(candidate_node)
                    if file_node == candidate_node_file or graph.graph.has_edge(candidate_node_file, file_node):
                        new_edges.append((candidate_node, class_node,
                                                  Edge(
                                                      relation=EdgeRelation.BaseClassOf,
                                                      location=None
                                                  )))
                    else:
                        file_package_list = [n for n in graph.graph.predecessors(file_node)
                                             if
                                             graph.graph[n][file_node][0]['relation'].relation == EdgeRelation.HasMember
                                             and n.type == NodeType.PACKAGE]
                        candidate_file_package_list = [n for n in graph.graph.predecessors(candidate_node_file)
                                                       if graph.graph[n][candidate_node_file][0][
                                                           'relation'].relation == EdgeRelation.HasMember
                                                       and n.type == NodeType.PACKAGE]
                        if len(file_package_list) > 0 and len(candidate_file_package_list) > 0:
                            if file_package_list[0] == candidate_file_package_list[0]:
                                new_edges.append((candidate_node, class_node,
                                                          Edge(
                                                              relation=EdgeRelation.BaseClassOf,
                                                              location=None
                                                          )))
        if 'implements' in class_node.attribute.keys():
            for implement_name in class_node.attribute['implements']:
                candidate_nodes = graph.get_nodes(
                    node_filter=lambda node: node.type == NodeType.INTERFACE and node.name == implement_name)
                if len(candidate_nodes) == 0:
                    continue
                for candidate_node in candidate_nodes:
                    candidate_node_file = graph.get_membership_parent(candidate_node)
                    if file_node == candidate_node_file or graph.graph.has_edge(candidate_node_file, file_node):
                        new_edges.append((candidate_node, class_node,
                                                  Edge(
                                                      relation=EdgeRelation.ImplementedBy,
                                                      location=None
                                                  )))
                    else:
                        file_package_list = [n for n in graph.graph.predecessors(file_node)
                                             if
                                             graph.graph[n][file_node][0]['relation'].relation == EdgeRelation.HasMember
                                             and n.type == NodeType.PACKAGE]
                        candidate_file_package_list = [n for n in graph.graph.predecessors(candidate_node_file)
                                                       if graph.graph[n][candidate_node_file][0][
                                                           'relation'].relation == EdgeRelation.HasMember
                                                       and n.type == NodeType.PACKAGE]
                        if len(file_package_list) > 0 and len(candidate_file_package_list) > 0:
                            if file_package_list[0] == candidate_file_package_list[0]:
                                new_edges.append((candidate_node, class_node,
                                                          Edge(
                                                              relation=EdgeRelation.ImplementedBy,
                                                              location=None
                                                          )))
        if 'uses' in class_node.attribute.keys():
            for use_name in class_node.attribute['uses']:
                if use_name not in all_class_name:
                    continue
                candidate_nodes = graph.get_nodes(
                    node_filter=lambda node: str(node.type) == NodeType.CLASS and node.name == use_name)
                if len(candidate_nodes) == 0:
                    continue
                for candidate_node in candidate_nodes:
                    candidate_node_file = graph.get_membership_parent(candidate_node)
                    if file_node == candidate_node_file or graph.graph.has_edge(candidate_node_file, file_node):
                        if not graph.graph.has_edge(candidate_node, class_node):
                            new_edges.append((candidate_node, class_node,
                                                      Edge(
                                                          relation=EdgeRelation.UsedBy,
                                                          location=None
                                                      )))
                    else:
                        file_package_list = [n for n in graph.graph.predecessors(file_node)
                                             if
                                             graph.graph[n][file_node][0]['relation'].relation == EdgeRelation.HasMember
                                             and n.type == NodeType.PACKAGE]
                        candidate_file_package_list = [n for n in graph.graph.predecessors(candidate_node_file)
                                                       if graph.graph[n][candidate_node_file][0][
                                                           'relation'].relation == EdgeRelation.HasMember
                                                       and n.type == NodeType.PACKAGE]
                        if len(file_package_list) > 0 and len(candidate_file_package_list) > 0:
                            if file_package_list[0] == candidate_file_package_list[0]:
                                if not graph.graph.has_edge(candidate_node, class_node):
                                    new_edges.append((candidate_node, class_node,
                                                      Edge(
                                                          relation=EdgeRelation.UsedBy,
                                                          location=None
                                                      )))
        return new_edges

    @staticmethod
    def incremental_generate_single_class(graph, all_class_name, class_node):

        new_edges = []
        file_node = graph.get_membership_parent(class_node)
        # 出边
        if 'extends' in class_node.attribute.keys():
            for extend_name in class_node.attribute['extends']:
                extend_name = extend_name.strip(')').strip('(').strip(':')
                candidate_nodes = graph.get_nodes(
                    node_filter=lambda node: node.type == NodeType.CLASS and node.name == extend_name)
                if len(candidate_nodes) == 0:
                    continue
                for candidate_node in candidate_nodes:
                    candidate_node_file = graph.get_membership_parent(candidate_node)
                    if file_node == candidate_node_file or graph.graph.has_edge(candidate_node_file, file_node):
                        new_edges.append((candidate_node, class_node,
                                                  Edge(
                                                      relation=EdgeRelation.BaseClassOf,
                                                      location=None
                                                  )))
                    else:
                        file_package_list = [n for n in graph.graph.predecessors(file_node)
                                             if
                                             graph.graph[n][file_node][0]['relation'].relation == EdgeRelation.HasMember
                                             and n.type == NodeType.PACKAGE]
                        candidate_file_package_list = [n for n in graph.graph.predecessors(candidate_node_file)
                                                       if graph.graph[n][candidate_node_file][0][
                                                           'relation'].relation == EdgeRelation.HasMember
                                                       and n.type == NodeType.PACKAGE]
                        if len(file_package_list) > 0 and len(candidate_file_package_list) > 0:
                            if file_package_list[0] == candidate_file_package_list[0]:
                                new_edges.append((candidate_node, class_node,
                                                          Edge(
                                                              relation=EdgeRelation.BaseClassOf,
                                                              location=None
                                                          )))
        if 'implements' in class_node.attribute.keys():
            for implement_name in class_node.attribute['implements']:
                candidate_nodes = graph.get_nodes(
                    node_filter=lambda node: node.type == NodeType.INTERFACE and node.name == implement_name)
                if len(candidate_nodes) == 0:
                    continue
                for candidate_node in candidate_nodes:
                    candidate_node_file = graph.get_membership_parent(candidate_node)
                    if file_node == candidate_node_file or graph.graph.has_edge(candidate_node_file, file_node):
                        new_edges.append((candidate_node, class_node,
                                                  Edge(
                                                      relation=EdgeRelation.ImplementedBy,
                                                      location=None
                                                  )))
                    else:
                        file_package_list = [n for n in graph.graph.predecessors(file_node)
                                             if
                                             graph.graph[n][file_node][0]['relation'].relation == EdgeRelation.HasMember
                                             and n.type == NodeType.PACKAGE]
                        candidate_file_package_list = [n for n in graph.graph.predecessors(candidate_node_file)
                                                       if graph.graph[n][candidate_node_file][0][
                                                           'relation'].relation == EdgeRelation.HasMember
                                                       and n.type == NodeType.PACKAGE]
                        if len(file_package_list) > 0 and len(candidate_file_package_list) > 0:
                            if file_package_list[0] == candidate_file_package_list[0]:
                                new_edges.append((candidate_node, class_node,
                                                          Edge(
                                                              relation=EdgeRelation.ImplementedBy,
                                                              location=None
                                                          )))
        if 'uses' in class_node.attribute.keys():
            for use_name in class_node.attribute['uses']:
                if use_name not in all_class_name:
                    continue
                candidate_nodes = graph.get_nodes(
                    node_filter=lambda node: str(node.type) == NodeType.CLASS and node.name == use_name)
                if len(candidate_nodes) == 0:
                    continue
                for candidate_node in candidate_nodes:
                    candidate_node_file = graph.get_membership_parent(candidate_node)
                    if file_node == candidate_node_file or graph.graph.has_edge(candidate_node_file, file_node):
                        if not graph.graph.has_edge(candidate_node, class_node):
                            new_edges.append((candidate_node, class_node,
                                                      Edge(
                                                          relation=EdgeRelation.UsedBy,
                                                          location=None
                                                      )))
                    else:
                        file_package_list = [n for n in graph.graph.predecessors(file_node)
                                             if
                                             graph.graph[n][file_node][0]['relation'].relation == EdgeRelation.HasMember
                                             and n.type == NodeType.PACKAGE]
                        candidate_file_package_list = [n for n in graph.graph.predecessors(candidate_node_file)
                                                       if graph.graph[n][candidate_node_file][0][
                                                           'relation'].relation == EdgeRelation.HasMember
                                                       and n.type == NodeType.PACKAGE]
                        if len(file_package_list) > 0 and len(candidate_file_package_list) > 0:
                            if file_package_list[0] == candidate_file_package_list[0]:
                                if not graph.graph.has_edge(candidate_node, class_node):
                                    new_edges.append((candidate_node, class_node,
                                                      Edge(
                                                          relation=EdgeRelation.UsedBy,
                                                          location=None
                                                      )))

        # 入边
        all_class_nodes = graph.get_nodes(node_filter=lambda node: node.type in [NodeType.CLASS, NodeType.ENUM, NodeType.STRUCTURE, NodeType.INTERFACE])
        for v in all_class_nodes:
            if 'extends' in v.attribute.keys():
                extend_name_list = [n.strip('(').strip(')') for n in v.attribute['extends']]
                if class_node.name in extend_name_list:
                    new_edges.append((class_node, v,
                                      Edge(
                                          relation=EdgeRelation.BaseClassOf,
                                          location=None
                                      )))
            if 'implements' in v.attribute.keys():
                if class_node.name in v.attribute['implements']:
                    new_edges.append((class_node, v,
                                      Edge(
                                          relation=EdgeRelation.ImplementedBy,
                                          location=None
                                      )))
            if 'uses' in v.attribute.keys():
                if class_node.name in v.attribute['uses']:
                    new_edges.append((class_node, v,
                                      Edge(
                                          relation=EdgeRelation.UsedBy,
                                          location=None
                                      )))
        return new_edges

    @staticmethod
    def generate_class_level_dependencies(graph: DependencyGraph, repo: Repository, changed_files=[]):
        if len(changed_files) == 0:
            all_class_nodes = graph.get_nodes(node_filter=lambda node: node.type == NodeType.CLASS or node.type == NodeType.ENUM or node.type == NodeType.STRUCTURE)
        else:
            all_class_nodes = graph.get_nodes(node_filter=lambda
                node: node.type in [NodeType.CLASS, NodeType.ENUM, NodeType.STRUCTURE, NodeType.INTERFACE] and str(node.location.file_path) in changed_files)
        all_class_name = [node.name for node in graph.get_nodes(node_filter=lambda node: node.type == NodeType.CLASS or node.type == NodeType.ENUM or node.type == NodeType.STRUCTURE)]
        max_processes = max(1, (os.cpu_count() or 1)//5)
        if len(changed_files) != 0:
            worker_partial = partial(TreeSitterDependencyGraphGenerator.incremental_generate_single_class, graph, all_class_name)
        else:
            worker_partial = partial(TreeSitterDependencyGraphGenerator.generate_single_class, graph, all_class_name)
        with ProcessPoolExecutor(max_processes) as executor:
            all_edges = list(tqdm(executor.map(worker_partial, all_class_nodes), total=len(all_class_nodes),
                                  desc="Generating class level dependencies"))
            for edges in all_edges:
                if edges:
                    graph.add_relational_edges_from(edges)
        return graph

    @staticmethod
    def generate_single_method(graph, method_node):
        new_edges = []
        class_node = graph.get_membership_parent(method_node)
        all_method_call = set(method_node.attribute['method_call_list'])
        for name in list(all_method_call):
            candidate_nodes = graph.get_nodes(
                node_filter=lambda node: node.name == name.strip("(") and node.type != NodeType.FILE and node.type != NodeType.PACKAGE)
            candidate_nodes.sort(key=lambda x: x.name)
            if len(candidate_nodes) == 0:
                continue
            for candidate_node in candidate_nodes:
                candidate_node_class = graph.get_membership_parent(candidate_node)
                if candidate_node_class.type == class_node.type:
                    if candidate_node_class == class_node or graph.graph.has_edge(candidate_node_class, class_node):
                        if not graph.graph.has_edge(candidate_node, method_node):
                            new_edges.append((candidate_node, method_node,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
                else:
                    if candidate_node_class.type != NodeType.FILE:
                        candidate_node_class = graph.get_membership_parent(candidate_node_class)
                    if class_node.type != NodeType.FILE:
                        class_node_parent = graph.get_membership_parent(class_node)
                    else:
                        class_node_parent = class_node
                    if candidate_node_class == class_node_parent or graph.graph.has_edge(candidate_node_class, class_node_parent):
                        if not graph.graph.has_edge(candidate_node, method_node):
                            new_edges.append((candidate_node, method_node,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
        method_body = method_node.location.get_text()
        successors = list(graph.graph.successors(class_node))
        successors.sort(key=lambda x: x.name)
        for node in successors:
            if node.type == NodeType.FIELD:
                clean_name = node.name.strip().strip(';')
                if clean_name not in method_body:
                    continue
                parent_field = graph.get_membership_parent(node)
                if parent_field == class_node:
                    new_edges.append((node, method_node,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
                elif graph.get_membership_parent(class_node):  # 内部类
                    pp = graph.get_membership_parent(class_node)
                    if parent_field == pp:
                        new_edges.append((node, method_node,
                                                  Edge(
                                                      relation=EdgeRelation.UsedBy,
                                                      location=None
                                                  )))
        file_node = graph.get_membership_parent(class_node)
        import_nodes = [source for source, target, data in graph.graph.in_edges(file_node, data=True) if
                        data['relation'].relation == EdgeRelation.ImportedBy and source.type == NodeType.VIRTUAL_API]
        for v in import_nodes:
            if v.name in method_body:
                if not graph.graph.has_edge(v, method_node):
                    new_edges.append((v, method_node,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
        return new_edges


    @staticmethod
    def incremental_generate_single_method(graph, method_node):
        
        new_edges = []
        #判断 method_node 是否存在于图中
        if not graph.graph.has_node(method_node):
            return new_edges
        # 出边
        class_node = graph.get_membership_parent(method_node)
        all_method_call = set(method_node.attribute['method_call_list'])
        for name in list(all_method_call):
            candidate_nodes = graph.get_nodes(
                node_filter=lambda node: node.name == name.strip("(") and node.type != NodeType.REPO and node.type != NodeType.FILE and node.type != NodeType.PACKAGE)
            candidate_nodes.sort(key=lambda x: x.name)
            if len(candidate_nodes) == 0:
                continue
            for candidate_node in candidate_nodes:
                candidate_node_class = graph.get_membership_parent(candidate_node)
                if candidate_node_class and candidate_node_class.type == class_node.type:
                    if candidate_node_class == class_node or graph.graph.has_edge(candidate_node_class, class_node):
                        if not graph.graph.has_edge(candidate_node, method_node):
                            new_edges.append((candidate_node, method_node,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
                elif candidate_node_class:
                    if candidate_node_class.type != NodeType.FILE:
                        candidate_node_class = graph.get_membership_parent(candidate_node_class)
                    if class_node.type != NodeType.FILE:
                        class_node_parent = graph.get_membership_parent(class_node)
                    else:
                        class_node_parent = class_node
                    if candidate_node_class == class_node_parent or graph.graph.has_edge(candidate_node_class,
                                                                                         class_node_parent):
                        if not graph.graph.has_edge(candidate_node, method_node):
                            new_edges.append((candidate_node, method_node,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
        method_body = method_node.location.get_text()
        successors = list(graph.graph.successors(class_node))
        successors.sort(key=lambda x: x.name)
        for node in successors:
            if node.type == NodeType.FIELD:
                clean_name = node.name.strip().strip(';')
                if clean_name not in method_body:
                    continue
                parent_field = graph.get_membership_parent(node)
                if parent_field == class_node:
                    new_edges.append((node, method_node,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
                elif graph.get_membership_parent(class_node):  # 内部类
                    pp = graph.get_membership_parent(class_node)
                    if parent_field == pp:
                        new_edges.append((node, method_node,
                                                  Edge(
                                                      relation=EdgeRelation.UsedBy,
                                                      location=None
                                                  )))
        file_node = graph.get_membership_parent(class_node)
        import_nodes = [source for source, target, data in graph.graph.in_edges(file_node, data=True) if
                        data['relation'].relation == EdgeRelation.ImportedBy and source.type == NodeType.VIRTUAL_API]
        for v in import_nodes:
            if v.name in method_body:
                if not graph.graph.has_edge(v, method_node):
                    new_edges.append((v, method_node,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))


        # 入边
        all_method_nodes = graph.get_nodes(node_filter=lambda node: node.type == NodeType.METHOD or node.type == NodeType.CONSTRUCTOR or node.type == NodeType.FUNCTION)
        for caller in all_method_nodes:
            if method_node.name in caller.attribute['method_call_list']:
                candidate_node_class = graph.get_membership_parent(caller)

                if candidate_node_class.type == class_node.type:
                    if candidate_node_class == class_node or graph.graph.has_edge(class_node, candidate_node_class):
                        if not graph.graph.has_edge(method_node, caller):
                            new_edges.append((method_node, caller,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
                else:
                    if candidate_node_class.type != NodeType.FILE:
                        candidate_node_class = graph.get_membership_parent(candidate_node_class)
                    if class_node.type != NodeType.FILE:
                        class_node_parent = graph.get_membership_parent(class_node)
                    else:
                        class_node_parent = class_node
                    if candidate_node_class == class_node_parent or graph.graph.has_edge(class_node_parent, candidate_node_class):
                        if not graph.graph.has_edge(method_node, caller):
                            new_edges.append((method_node, caller,
                                              Edge(
                                                  relation=EdgeRelation.UsedBy,
                                                  location=None
                                              )))
        return new_edges

    @staticmethod
    def generate_method_level_dependencies(graph: DependencyGraph, repo: Repository, changed_files=[]):
        if len(changed_files) == 0:
            all_method_nodes = graph.get_nodes(
                node_filter=lambda node: node.type == NodeType.METHOD or node.type == NodeType.CONSTRUCTOR or node.type == NodeType.FUNCTION)
        else:
            all_method_nodes = graph.get_nodes(
                node_filter=lambda
                    node: node.type in [NodeType.METHOD, NodeType.CONSTRUCTOR, NodeType.FUNCTION] and str(node.location.file_path) in changed_files)
        all_method_nodes.sort(key=lambda x: x.name)
        max_processes = max(1, (os.cpu_count() or 1)//5)
        if changed_files:
            worker_partial = partial(TreeSitterDependencyGraphGenerator.incremental_generate_single_method, graph)
        else:
            worker_partial = partial(TreeSitterDependencyGraphGenerator.generate_single_method, graph)
        with ProcessPoolExecutor(max_processes) as executor:
            all_edges = list(tqdm(executor.map(worker_partial, all_method_nodes), total=len(all_method_nodes),
                                  desc="Generating method level dependencies"))
            for edges in all_edges:
                if edges:
                    graph.add_relational_edges_from(edges)
        return graph
    
    @staticmethod
    def incremental_update_graph(old_graph: DependencyGraph, changed_files, new_repo_path: str, target_language):
        # 删除已经修改的节点
        all_nodes = [v for v in old_graph.get_nodes()]
        for v in all_nodes:
            if v.location and any([str(v.location.file_path) in changed_files["added"],
                                  str(v.location.file_path) in changed_files["modified"],
                                  str(v.location.file_path) in changed_files["deleted"]]):
                old_graph.graph.remove_node(v)
        # 更新已有节点的路径
        # all_nodes = [v for v in old_graph.graph.nodes]
        # for v in all_nodes:
        #     if v.location:
        #         v.location.file_path = Path(str(v.location.file_path).replace(old_repo_path, new_repo_path, 1))

        # 加入新增的节点
        changed_files_list = changed_files["added"] + changed_files["modified"]
        if len(changed_files_list) == 0:
            return old_graph
        repo = Repository(new_repo_path, target_language)
        graph = copy.deepcopy(old_graph)
        graph = TreeSitterDependencyGraphGenerator.generate_membership_hierarchical(graph, repo, changed_files=changed_files_list)
        # graph = TreeSitterDependencyGraphGenerator.generate_file_level_dependencies(graph, repo, changed_files=changed_files_list)
        # graph = TreeSitterDependencyGraphGenerator.generate_class_level_dependencies(graph, repo, changed_files=changed_files_list)
        # graph = TreeSitterDependencyGraphGenerator.generate_method_level_dependencies(graph, repo, changed_files=changed_files_list)
        return graph

    def incremental_update_graph_with_dependency(self, old_graph: DependencyGraph, changed_files):
        repo = Repository(old_graph.repo_path, old_graph.language)
        graph = self.generate_file_level_dependencies(old_graph, repo, changed_files=[str(f) for f in changed_files])
        graph = TreeSitterDependencyGraphGenerator.generate_class_level_dependencies(graph, repo, changed_files=[str(f) for f in changed_files])
        graph = TreeSitterDependencyGraphGenerator.generate_method_level_dependencies(graph, repo, changed_files=[str(f) for f in changed_files])
        return graph


def get_diff_files(original_folder, modified_folder):
    """
    比较两个文件夹，返回内容有变化的文件列表。

    :param original_folder: 旧版本代码的文件夹
    :param modified_folder: 修改后代码的文件夹
    :return: 变更文件列表
    """
    diff_files = []

    # 遍历原始目录
    for root, _, files in os.walk(original_folder):
        for file in files:
            orig_file = os.path.join(root, file)
            mod_file = orig_file.replace(original_folder, modified_folder, 1)

            if os.path.exists(mod_file):
                # 文件存在于修改后的目录，检查内容是否相同
                if not filecmp.cmp(orig_file, mod_file, shallow=False):
                    diff_files.append(mod_file)
            else:
                # 文件在新版本中被删除
                diff_files.append(orig_file)

    # 检查新文件（新增的文件）
    for root, _, files in os.walk(modified_folder):
        for file in files:
            mod_file = os.path.join(root, file)
            orig_file = mod_file.replace(modified_folder, original_folder, 1)

            if not os.path.exists(orig_file):
                # 新增文件
                diff_files.append(mod_file)

    return diff_files


if __name__ == "__main__":

    root_path = Path('/Users/bytedance/Desktop/issue_resolving/RepoQueryBench')
    repo = Repository(root_path, Language.Python)
    D = DependencyGraph(repo.repo_path, repo.language)
    graph = TreeSitterDependencyGraphGenerator.generate_membership_hierarchical(D, repo)
    graph = TreeSitterDependencyGraphGenerator.generate_file_level_dependencies(graph, repo)
    graph = TreeSitterDependencyGraphGenerator.generate_class_level_dependencies(graph, repo)
    graph = TreeSitterDependencyGraphGenerator.generate_method_level_dependencies(graph, repo)
    print("=" * 50, "nodes")
    node_list = list(graph.get_nodes())
    for node in node_list:
        print("--"*10)
        print(node.name, node.type, node.location, node.attribute)
    for edge in graph.get_edges():
        if edge[0].location and edge[1].location:
            # detect cross file edge
            if edge[0].location.file_path != edge[1].location.file_path:
                print(edge[0].name, edge[0].type, edge[0].location, '->', edge[1].name, edge[1].type, edge[1].location, edge[2])
