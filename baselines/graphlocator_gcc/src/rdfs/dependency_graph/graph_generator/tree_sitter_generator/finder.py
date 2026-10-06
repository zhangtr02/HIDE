from functools import lru_cache
from pathlib import Path
from textwrap import dedent
from typing import Any
from tree_sitter import Parser, Language as TS_Language, Node as TS_Node, Tree
from rdfs.dependency_graph.graph_generator.tree_sitter_generator.load_lib import (
    get_builtin_lib_path,
)
from rdfs.dependency_graph.models.language import Language
from rdfs.dependency_graph.utils.read_file import read_file_to_string

"""
Tree-sitter query to find all the imports in a code. The captured import name should be named as `import_name`.
"""
FIND_IMPORT_QUERY = {
    Language.Python: dedent(
        """
        [
          (import_from_statement) @import_name
          (import_statement) @import_name
        ]
        """
    ),
    Language.Java: dedent(
        """
        (import_declaration
        [
          (identifier) @import_name
          (scoped_identifier) @import_name
        ])
        """
    ),
    Language.Kotlin: dedent(
        """
        (import_header (identifier) @import_name)
        """
    ),
    Language.CSharp: dedent(
        """
        (using_directive
        [
          (qualified_name) @import_name
          (identifier) @import_name
        ])
        """
    ),
    # Language.TypeScript: dedent(
    #     """
    #     [
    #         (import_statement (string (string_fragment) @import_name))
    #         (call_expression
    #           function: ((identifier) @require_name
    #                         (#eq? @require_name "require"))
    #           arguments: (arguments (string (string_fragment) @import_name))
    #         )
    #     ]
    #     """
    # ),
    Language.TypeScript: dedent(
        """
        [
            (import_statement) @import_name
        ]
        """
    ),
    Language.ArkTS: dedent(
        """
        [
            (import_statement) @import_name
        ]
        """
    ),
    Language.JavaScript: dedent(
        """
        [
            (import_statement (string (string_fragment) @import_name))
            (call_expression
              function: ((identifier) @require_name
                            (#eq? @require_name "require"))
              arguments: (arguments (string (string_fragment) @import_name))
            )
        ]
        """
    ),
    Language.PHP: dedent(
        """
        [
          (require_once_expression (string) @import_name)
          (require_expression (string) @import_name)
          (include_expression (string) @import_name)
        ]
        """
    ),
    Language.Ruby: dedent(
        """
        (call
            method: ((identifier) @require_name
                (#match? @require_name "require_relative|require")
            )
            arguments: (argument_list) @import_name
        )
        """
    ),
    Language.C: dedent(
        """
        (preproc_include path: 
            [
                (string_literal) @import_name
                (system_lib_string) @import_name
            ]
        )
        """
    ),
    Language.CPP: dedent(
        """
        (preproc_include path: 
            [
                (string_literal) @import_name
                (system_lib_string) @import_name
            ]
        )
        """
    ),
    Language.Go: dedent(
        """
        (import_declaration
            [
                (import_spec path: (interpreted_string_literal) @import_name)
                (import_spec_list (import_spec path: (interpreted_string_literal) @import_name))
            ]
        )
        """
    ),
    Language.Swift: dedent(
        """
        (import_declaration (identifier) @import_name)
        """
    ),
    Language.Rust: dedent(
        """
        [
            (use_declaration argument: (scoped_identifier) @import_name)
            (use_declaration argument: (use_as_clause path: (scoped_identifier) @import_name))
        ]
        """
    ),
    Language.Lua: dedent(
        """
        (call
            function:
                (variable
                    name: ((identifier) @require_name)
                            (#match? @require_name "require|dofile|loadfile"))
            arguments:
                (argument_list [(expression_list)(string)] @import_name)
        )
        """
    ),
    Language.Bash: dedent(
        """
        (command
            name: ((command_name) @command_name
                    (#match? @command_name "\\\\.|source|bash|zsh|ksh|zsh|csh|dash"))
            argument: (word) @import_name
        )
        """
    ),
    Language.R: dedent(
        """
        (call
            function: ((identifier) @source_name)
                       (#eq? @source_name "source")
            arguments: (arguments (argument) @import_name)
        )
        """
    ),
}

FIND_IMPORT_ATTRIBUTE_QUERY = {
    Language.TypeScript: dedent(
        """
        [   
            (identifier) @import_name
            (import_statement (string (string_fragment) @import_from))
        ]
        """
    ),
    Language.Python: dedent(
        """
        (identifier) @import_name
        """
    ),
    Language.ArkTS: dedent(
        """
        [   
            (identifier) @import_name
            (import_statement (string (string_fragment) @import_from))
        ]
        """
    ),
    Language.Rust: dedent(
        """
        (identifier) @import_name
        """
    ),
}

"""
Tree-sitter query to find all the package in a code. The captured pacakge name should be named as `package_name`.
Note that not all languages have packages declared in code.
"""
FIND_PACKAGE_QUERY = {
    Language.Java: dedent(
        """
        (package_declaration
        [
          (identifier) @package_name
          (scoped_identifier) @package_name
        ])
        """
    ),
    Language.Kotlin: dedent(
        """
        (package_header (identifier) @package_name)
        """
    ),
    Language.CSharp: dedent(
        """
        (namespace_declaration
        [
          (qualified_name) @package_name
          (identifier) @package_name
        ])
        """
    ),
    Language.Go: dedent(
        """
        (package_clause (package_identifier) @package_name)
        """
    ),
}


class ImportFinder:
    def __init__(self, language: Language):
        lib_path = get_builtin_lib_path()
        self.language = language
        # Initialize the Tree-sitter language
        self.parser = Parser()
        self.ts_language = TS_Language(str(lib_path.absolute()), str(language))
        self.parser.set_language(self.ts_language)

    def _query_and_captures(
            self, code: str, query: str, capture_name="import_name"
    ) -> list[TS_Node]:
        """
        Query the Tree-sitter language and get the nodes that match the query
        :param code: The code to be parsed
        :param query: The query to be matched
        :param capture_name: The name of the capture group to be matched
        :return: The nodes that match the query
        """
        tree: Tree = self.parser.parse(code.encode())
        query = self.ts_language.query(query)
        captures = query.captures(tree.root_node)
        return [node for node, captured in captures if captured == capture_name]

    @lru_cache(maxsize=256)
    def find_imports(
            self,
            code: str
    ) -> list[TS_Node]:
        all_imports = self._query_and_captures(code, FIND_IMPORT_QUERY[self.language])
        all_imports_str = []
        for node in all_imports:
            match self.language:
                case Language.Java:
                    full_name = node.text.decode('utf-8')
                    full_name_list = full_name.split('.')
                    all_imports_str.append((".".join(full_name_list[:-1]), [full_name_list[-1]]))
                case Language.TypeScript:
                    query = self.ts_language.query(FIND_IMPORT_ATTRIBUTE_QUERY[self.language])
                    captures = query.captures(node)
                    package_name = ""
                    api_name = []
                    find_nodes = set([(node, captured) for node, captured in captures])
                    for v, n in find_nodes:
                        if n == "import_from":
                            package_name = v.text.decode('utf-8')
                        elif n == "import_name":
                            api_name.append(v.text.decode('utf-8'))

                    all_imports_str.append((package_name, api_name))
                case Language.CSharp:
                    full_name = node.text.decode('utf-8')
                    all_imports_str.append((full_name, [""]))
                case Language.Python:
                    if 'relative_import' in [nc.type for nc in node.children]:
                        if node.children[1].type == 'relative_import':
                            package_name = node.children[1].text.decode('utf-8')
                            query = self.ts_language.query(FIND_IMPORT_ATTRIBUTE_QUERY[self.language])
                            captures = query.captures(node)
                            find_nodes = set([(node, captured) for node, captured in captures])
                            for v, n in find_nodes:
                                if (package_name, [v.text.decode('utf-8')]) not in all_imports_str:
                                    all_imports_str.append((package_name, [v.text.decode('utf-8')]))
                    elif node.type == "import_from_statement":
                        package_name = node.child_by_field_name('module_name').text.decode()
                        api_node_list = node.children_by_field_name('name')
                        for v in api_node_list:
                            all_imports_str.append((package_name, [v.text.decode('utf-8')]))
                    else:
                        package_name = ""
                        query = self.ts_language.query(FIND_IMPORT_ATTRIBUTE_QUERY[self.language])
                        captures = query.captures(node)
                        find_nodes = set([(node, captured) for node, captured in captures])
                        for v, n in find_nodes:
                            if (package_name, [v.text.decode('utf-8')]) not in all_imports_str:
                                all_imports_str.append((package_name, [v.text.decode('utf-8')]))
                case Language.C | Language.CPP:
                    full_name = node.text.decode('utf-8')
                    cleaned_name = full_name[1:-1]
                    if "/" in cleaned_name:
                        cleaned_name = cleaned_name.split("/")[-1]
                    if "." in cleaned_name:
                        cleaned_name = cleaned_name.split(".")[0]
                    all_imports_str.append(("", [cleaned_name]))
                case Language.Rust:
                    query = self.ts_language.query(FIND_IMPORT_ATTRIBUTE_QUERY[self.language])
                    captures = query.captures(node)
                    package_name = ""
                    find_nodes = set([(node, captured) for node, captured in captures])
                    for v, n in find_nodes:
                        if (package_name, [v.text.decode('utf-8')]) not in all_imports_str:
                            all_imports_str.append((package_name, [v.text.decode('utf-8')]))
        return all_imports_str


    @lru_cache(maxsize=256)
    def find_module_name(self, code: str, file_path) -> str | None:
        """
        Find the name of the module of the current file.
        This term is broad enough to encompass the different ways in which these languages organize and reference code units
        In Java, it is the name of the package.
        In C#, it is the name of the namespace.
        In JavaScript/TypeScript, it is the name of the file.
        """
        # Use read_file_to_string here to avoid non-UTF8 decoding issue
        match self.language:
            case Language.Java | Language.Kotlin:
                captures = self._query_and_captures(
                    code, FIND_PACKAGE_QUERY[self.language], "package_name"
                )

                if len(captures) > 0:
                    node = captures[0]
                    package_name = node.text.decode()
                    module_name = f"{package_name}.{file_path.stem}"
                    return module_name
            case Language.CSharp | Language.Go:
                captures = self._query_and_captures(
                    code, FIND_PACKAGE_QUERY[self.language], "package_name"
                )
                if len(captures) > 0:
                    node = captures[0]
                    package_name = node.text.decode()
                    return package_name
            case (
            Language.TypeScript
            | Language.JavaScript
            | Language.Python
            | Language.Ruby
            | Language.Rust
            | Language.Lua
            | Language.R
            | Language.C
            | Language.CPP
            ):
                return file_path.stem
            case Language.PHP | Language.Bash:
                return file_path.name
            case Language.Swift:
                # Swift module name is its parent directory
                return file_path.parent.name
            case _:
                raise NotImplementedError(f"Language {self.language} is not supported")

    @lru_cache(maxsize=256)
    def find_package_name(self, file_path: Path) -> str | None:
        """
        Find the name of the module of the current file.
        This term is broad enough to encompass the different ways in which these languages organize and reference code units
        In Java, it is the name of the package.
        In C#, it is the name of the namespace.
        In JavaScript/TypeScript, it is the name of the file.
        """
        # Use read_file_to_string here to avoid non-UTF8 decoding issue
        code = read_file_to_string(file_path)
        match self.language:
            case Language.Java | Language.Kotlin:
                captures = self._query_and_captures(
                    code, FIND_PACKAGE_QUERY[self.language], "package_name"
                )

                if len(captures) > 0:
                    node = captures[0]
                    package_name = node.text.decode()
                    return package_name
            case Language.CSharp | Language.Go:
                captures = self._query_and_captures(
                    code, FIND_PACKAGE_QUERY[self.language], "package_name"
                )
                if len(captures) > 0:
                    node = captures[0]
                    package_name = node.text.decode()
                    return package_name
            case (
            Language.TypeScript
            | Language.JavaScript
            | Language.Python
            | Language.Ruby
            | Language.Rust
            | Language.Lua
            | Language.R
            | Language.C
            | Language.CPP
            ):
                return file_path.stem
            case Language.PHP | Language.Bash:
                return file_path.name
            case Language.Swift:
                # Swift module name is its parent directory
                return file_path.parent.name
            case _:
                raise NotImplementedError(f"Language {self.language} is not supported")


"""
Tree-sitter query to find all the class/enum/interface in a code. The captured class name should be named as `class_name`.
"""
FIND_CLASS_QUERY = {
    Language.Java: dedent(
        """[
            (class_declaration) @class
            (interface_declaration) @interface
            (enum_declaration) @enum
            ]
        """
    ),
    Language.TypeScript: dedent(
        """[
            (abstract_class_declaration) @class
            (class_declaration) @class
            (interface_declaration) @interface
            (enum_declaration) @enum
            ]
        """
    ),
    Language.ArkTS: dedent(
        """[
            (class_declaration) @class
            (interface_declaration) @interface
            (enum_declaration) @enum
            ]
        """
    ),
    Language.CSharp: dedent(
        """[
            (class_declaration) @class
            (interface_declaration) @interface
            (enum_declaration) @enum
            ]
        """
    ),
    Language.C: dedent(
        """[
            (struct_specifier
                name: (type_identifier)) @struct
            (enum_specifier
                name: (type_identifier)) @enum
            (type_definition
                type: (struct_specifier)) @struct
            (type_definition
                type: (enum_specifier)) @enum
            ]
        """
    ),
    Language.CPP: dedent(
        """[
            (class_specifier
                name: (type_identifier)) @class
            (struct_specifier
                name: (type_identifier)) @struct
            (enum_specifier
                name: (type_identifier)) @enum
            (type_definition
                type: (class_specifier)) @class
            (type_definition
                type: (struct_specifier)) @struct
            (type_definition
                type: (enum_specifier)) @enum
            ]
        """
    ),
    Language.Rust: dedent(
        """[
            (struct_item
                name: (type_identifier)) @struct
            (enum_item
                name: (type_identifier)) @enum
            ]
        """
    ),
    Language.Python: dedent(
        """
            (class_definition) @class
        """
    )
}

FIND_CLASS_ATTRIBUTE_QUERY = {
    Language.Java: dedent("""[
          (class_declaration
              
            name: (identifier) @name
        
            (superclass
                (type_identifier) @extends)?
                
            (super_interfaces
                (type_list
                    (type_identifier) @implements))?
          )
          (interface_declaration
          
            name: (identifier) @name
        
            (superclass
                (type_identifier) @extends)?
          )
          (enum_declaration
              
            name: (identifier) @name
        
            (super_interfaces
                (type_list
                    (type_identifier) @implements))?
          )
    ]
    """),
    Language.TypeScript: dedent("""[
          (class_declaration
            
            (type_identifier) @name
            
            (class_heritage
                (extends_clause
                    (identifier) @extends))?
            
            (class_heritage
                (implements_clause
                    (type_identifier) @implements))?
          )
          (interface_declaration
          
            (type_identifier) @name
            
            (class_heritage
                (extends_clause
                    (identifier) @extends))?
          )
          (enum_declaration
          
            (identifier) @name
            
          )
    ]
    """),
    Language.ArkTS: dedent("""[
      (class_declaration

        (type_identifier) @name

        (class_heritage
            (extends_clause
                (identifier) @extends))?

        (class_heritage
            (implements_clause
                (type_identifier) @implements))?
      )
      (interface_declaration

        (type_identifier) @name

        (class_heritage
            (extends_clause
                (identifier) @extends))?
      )
      (enum_declaration

        (identifier) @name

      )
]
"""),
    Language.CSharp: dedent("""[
          (class_declaration

            (base_list
                (identifier) @extends)?
          )
          (interface_declaration

            (base_list
                (identifier) @extends)?
          )
    ]
    """),
    Language.Python: dedent("""
        (class_definition
            (argument_list
                (identifier) @extends))
    """),
}

FIND_CLASS_USE_QUERY = {
    Language.Java: dedent(
        """
        [
        (type_identifier) @use
        (method_invocation
            object: (identifier) @use)
        ]
        """
    ),
    Language.TypeScript: dedent(
        """
        (type_identifier) @use
        """
    ),
    Language.ArkTS: dedent(
        """
        (type_identifier) @use
        """
    ),
    Language.CSharp: dedent(
        """
        [
        ((identifier) @use)?
        ((qualified_name) @use)?
        ]
        """
    ),
    Language.Python: dedent(
        """
        [
        (call
            function: (identifier) @use)
        (call
            function: (attribute
              attribute: (identifier) @use)
        )
        ]
        """
    ),
}

FIND_FIELD_QUERY = {
    Language.Java: dedent(
        """
        (class_body
            (field_declaration) @field)*
        """
    ),
    Language.TypeScript: dedent(
        """
        (class_body
            (public_field_definition) @field)*
        """
    ),
    Language.ArkTS: dedent(
        """
        (class_body
            (public_field_definition) @field)*
        """
    ),
    Language.CSharp: dedent(
        """
        (declaration_list
            (field_declaration) @field)*
        """
    ),
    Language.Python: dedent(
        """
        (attribute
            object: (identifier)) @field
        """
    ),
}

FIND_FIELD_NAME_QUERY = {
    Language.Java: dedent(
        """
        (variable_declarator 
            (identifier) @field.name)*
        """
    ),
    Language.TypeScript: dedent(
        """
        (property_identifier) @field.name
        """
    ),
    Language.ArkTS: dedent(
        """
        (property_identifier) @field.name
        """
    ),
    Language.CSharp: dedent(
        """
        (variable_declarator 
            name: (identifier) @field.name)
        """
    ),
}

FIND_METHOD_QUERY = {
    Language.Java: dedent(
        """
        [(method_declaration) @method
        (constructor_declaration) @constructor]
        """
    ),
    Language.TypeScript: dedent(
            """
            [
            (class_body
                  (method_definition) @method
            )
            (function_declaration) @method
            ]
            """
    ),
    Language.ArkTS: dedent(
        """
        [
        (class_body
              (method_definition) @method
        )
        (function_declaration) @method
        ]
        """
    ),
    Language.CSharp: dedent(
        """
        [(method_declaration) @method
        (constructor_declaration) @constructor]
        """
    ),
    Language.C: dedent(
        """
        (function_definition) @method
        """
    ),
    Language.CPP: dedent(
        """
        (function_definition) @method
        """
    ),
    Language.Rust: dedent(
        """
        (function_item) @method
        """
    ),
    Language.Python: dedent(
        """
        (function_definition) @method
        """
    ),
}

FIND_IMPL_BLOCK = {
    Language.Rust: dedent(
        """
        (impl_item) @impl
        """
    ),
}
FIND_METHOD_CALL_QUERY = {
    Language.Java: dedent(
        """
        (method_invocation name: (identifier) @name)
        """
    ),
    Language.TypeScript: dedent(
        """
        (call_expression
            (member_expression
                (property_identifier) @name)*
        )
        """
    ),
    Language.ArkTS: dedent(
        """
        (call_expression
            (member_expression
                (property_identifier) @name)*
        )
        """
    ),
    Language.CSharp: dedent(
        """
        (invocation_expression function: (identifier) @name)
        """
    ),
    Language.Python: dedent(
        """
        [
          (call
            function: (attribute
              attribute: (identifier) @name
            )
          )
          (call
            function: (identifier) @name
          )
        ]
        """
    ),
    Language.C: dedent(
        """
        [
          (call_expression
            function: (identifier) @name)
          (call_expression
            function: (field_expression
              field: (field_identifier) @name))
        ]
        """
    ),
    Language.CPP: dedent(
        """
        [
          (call_expression
            function: (identifier) @name)
          (call_expression
            function: (field_expression
              field: (field_identifier) @name))
          (call_expression
            function: (qualified_identifier
              name: (identifier) @name))
        ]
        """
    ),
}

FIND_PREPROC_DEF_QUERY = {
    Language.C: dedent(
        """
        (preproc_def) @preproc_def
        """
    ),
    Language.CPP: dedent(
        """
        (preproc_def) @preproc_def
        """
    ),
}

FIND_GLOBAL_VAR_QUERY = {
    Language.C: dedent(
        """
        [
        (translation_unit
            (declaration
                declarator: (init_declarator)) @global_var)
        (translation_unit
            (preproc_if
                (declaration
                    declarator: (init_declarator)) @global_var))
        ] 
        """
    ),
    Language.CPP: dedent(
        """
        [
        (translation_unit
            (declaration
                declarator: (init_declarator)) @global_var)
        (translation_unit
            (preproc_if
                (declaration
                    declarator: (init_declarator)) @global_var))
        ] 
        """
    ),
    Language.Rust: dedent(
        """
        (const_item) @global_var
        """
    ),
    Language.Python: dedent(
        """
        (module
            (expression_statement
                (assignment) @global_var))
        """
    ),
}


FIND_IDENTIFIER_QUERY = {
    Language.C: dedent(
        """
        (identifier) @identifier
        """
    ),
    Language.CPP: dedent(
        """
        (identifier) @identifier
        """
    ),
    Language.Rust: dedent(
        """
        (identifier) @identifier
        """
    ),
    Language.Python: dedent(
        """
        (identifier) @identifier
        """
    ),
}

FIND_TYPE_IDENTIFIER_QUERY = {
    Language.C: dedent(
        """
        (type_identifier) @identifier
        """
    ),
    Language.CPP: dedent(
        """
        [
        (type_identifier) @identifier
        (field_identifier) @identifier
        ]
        """
    ),
    Language.Rust: dedent(
        """
        [
        (type_identifier) @identifier
        (field_identifier) @identifier
        ]
        """
    ),
}


FIND_FUNCTION_QUERY = {
    Language.Python: dedent(
        """
        [
        (module
            (function_definition) @func)
        (module
            (decorated_definition
                (function_definition) @func))
        ]
        """
    ),
    Language.C: dedent(
        """
        (function_definition) @method
        """
    ),
    Language.CPP: dedent(
        """
        (function_definition) @method
        """
    ),
    Language.Rust: dedent(
        """
        (function_item) @method
        """
    ),
}

class FileFinder:
    def __init__(self, language: Language, file_code: str):
        lib_path = get_builtin_lib_path()
        self.language = language
        # Initialize the Tree-sitter language
        self.parser = Parser()
        if language == Language.ArkTS:
            self.ts_language = TS_Language(str(lib_path.absolute()), str(Language.TypeScript))
        else:
            self.ts_language = TS_Language(str(lib_path.absolute()), language)
        self.parser.set_language(self.ts_language)
        self.tree: Tree = self.parser.parse(file_code.encode())

    def query_and_captures(self, query: str, root_node: TS_Node, capture_name=None) -> set[Any] | set[tuple[Any, Any]]:
        """
        Query the Tree-sitter language and get the nodes that match the query
        :param query: The query to be matched
        :param capture_name: The name of the capture group to be matched
        :return: The nodes that match the query
        """
        if root_node is None:
            return set()
        query = self.ts_language.query(query)
        captures = query.captures(root_node)

        if capture_name is None:
            return set([node for node, captured in captures])
        else:
            return set([(node, captured) for node, captured in captures if captured in capture_name])

    def get_tree(self):
        return self.tree
