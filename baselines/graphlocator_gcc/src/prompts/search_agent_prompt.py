GET_SEED_LOC_INSTRUCTION = """
# Task:
You will be provided with a GitHub problem description and you have a hierarchical graph representation of current code repository that includes the following levels: DIRECTORY, FILE, CLASS, METHOD, FUNCTION, FIELD, STRUCTURE, GLOBAL_VAR, and so on. The relationship between these levels is expressed using `HasMember`.
Your objective is to localize the specific files, classes, structures, functions, or statement in this graph representation that require modification or contain essential information to resolve the issue.

1. Analyze the issue: Understand the problem described in the issue and identify what might be causing it.
2. List all potential keywords for searching from the issue and then call retrieval-based tools one by one.
3. If there are no any other keywords to search, use the Finish tool to indicate the end of the search.
4. Do not use the '.' in the search name.
"""


IS_RELEVANT_INSTRUCTION = """
# Task:
Please look through the following GitHub problem description and the code element list.
Your objective is to judge whether each code element is the cause of the issue.

1. Analyze the issue: Understand the problem described in the issue and analyze the code elements what may cause it.
2. For each code element, if it is selected as **irrelevant** to resolve the issue, return **False**, otherwise return **True**. Results should be separated by new lines and wrapped with ```
3. Only use the markdown code block format to return the results, without additional text. Do not use it in explanation.
For example:
```
True
False
```
"""


_NODE_TYPES = "[DIRECTORY, FILE, CLASS, METHOD, FUNCTION, FIELD, STRUCTURE, ENUM, GLOBAL_VAR, BODY]"

_SEARCH_NODE_DESCRIPTION = f"""Search the codebase to retrieve relevant code element based on given queries(code element type and name).
** Note:
- Either `node_type` or `node_name` must be provided to perform a search.
- The `node_type` must be chosen from {_NODE_TYPES}, where BODY signifies function or method body.
- If the you are note sure for part of the parameters, you can use "*" to represent a wildcard, which will match any type or name.

** Example Usage:
# Search for a GCC source file
search_node(node_type='FILE', node_name='pt.cc')

# Search for a structure
search_node(node_type='STRUCTURE', node_name='tree_node')

# Search for all nodes whose name is `TargetName`
search_node(node_type='*', node_name='TargetName')
"""


_SEARCH_EDGE_DESCRIPTION = f"""Search the codebase to retrieve a set of pairs of code elements that has a specific dependency relationship.
** Note:
- At least one of the parameters is provided to perform a search.
- The `node_type` must be chosen from {_NODE_TYPES}, where BODY signifies function or method body.
- The `edge_type` must be chosen from [ImportedBy, BaseClassOf, UsedBy, HasMember, ImplementedBy].
- `src_node_type` `src_node_name` `edge_type` `trg_node_type` `trg_node_name` represents that `src_node_name` of type `src_node_type` is `edge_type` `trg_node_name` of type `trg_node_type`.
- If the you are note sure for part of the parameters, you can use "*" to represent a wildcard, which will match any type or name.

** Example Usage:
# Search for a structure that has a function member
search_edge(src_node_type='STRUCTURE', src_node_name='tree_node', edge_type='HasMember', trg_node_type='FUNCTION', trg_node_name='instantiate_decl')

# Search for all functions that use the function `instantiate_decl`
search_edge(src_node_type='FUNCTION', src_node_name='instantiate_decl', edge_type='UsedBy', trg_node_type='FUNCTION', trg_node_name='*')

# Search for a function that has ICE-related text in its body
search_edge(src_node_type='FUNCTION', src_node_name='*', edge_type='HasMember', trg_node_type='BODY', trg_node_name='internal_error')
"""


SearchNodeTool = {
    "type": "function",
    "function": {
        "name": "search_node",
        "description": _SEARCH_NODE_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "node_type": {
                    "type": "string",
                    "description": f"The node type to search for within the codebase. The value must be chosen from {_NODE_TYPES}. If unsure, use '*'.",
                },
                "node_name": {
                    "type": "string",
                    "description": "Specific node name to locate corresponding code element of type node_type within codebase. If unsure, use '*'.",
                },
            },
            "required": [],
        },
    },
}


SearchEdgeTool = {
    "type": "function",
    "function": {
        "name": "search_edge",
        "description": _SEARCH_EDGE_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "src_node_type": {
                    "type": "string",
                    "description": f"The source node type. The value must be chosen from {_NODE_TYPES}. If unsure, use '*'.",
                },
                "src_node_name": {
                    "type": "string",
                    "description": "The source node name. If unsure, use '*'.",
                },
                "edge_type": {
                    "type": "string",
                    "description": "The edge type. The value must be chosen from [ImportedBy, BaseClassOf, UsedBy, HasMember, ImplementedBy]. If unsure, use '*'.",
                },
                "trg_node_type": {
                    "type": "string",
                    "description": f"The target node type. The value must be chosen from {_NODE_TYPES}. If unsure, use '*'.",
                },
                "trg_node_name": {
                    "type": "string",
                    "description": "The target node name. If unsure, use '*'.",
                },
            },
            "required": [],
        },
    },
}


FinishTool = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Finish the interaction when the task is complete OR if the assistant cannot proceed further with the task.",
        "parameters": {
            "type": "object",
            "properties": {
                "finished": {
                    "type": "boolean",
                    "description": "Set to be True if the interaction is finished.",
                }
            },
        },
    },
}
