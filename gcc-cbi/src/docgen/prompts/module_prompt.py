from __future__ import annotations

import json
from typing import Any, Dict, List


SYS_MODULE_DOC = """You generate responsibility documentation for one GCC module used by compiler bug localization.

Task:
Given the file list and available file docs for one GCC localization module, generate a module doc for target_module.

Input context:
- target_module is a GCC localization module. It may be a real GCC directory such as gcc/cp or gcc/fortran, a target-specific directory such as gcc/config/i386, or a virtual root module such as gcc/root/tree-ssa or gcc/root/gimple.
- module_file_paths lists all accumulated source-like file docs currently known for this module across the prepared GCC commits.
- file_docs contains reusable responsibility docs for files in this module when available.

Output JSON only.

Schema:
{
  "docs": [
    {
      "id": "gcc/<module>",
      "responsibility": "module-level functional responsibility"
    }
  ]
}

Field requirements:
- id:
  Must exactly match target_module.
- responsibility:
  Describe this module's stable functional responsibility in GCC, in one coherent paragraph.
  Explain the compiler stage or subsystem represented by this module and how its main file groups contribute to that role.
  Use file_docs first when available, then module id and file paths as supporting evidence.
  Cover the major functionality represented by the module without listing every file one by one.
  Mention important subareas, recurring filename families, major compiler concepts, language rules, target behavior, diagnostics, lowering, analysis, transformation, emission, or support logic when they define the module.
  Make the description useful for distinguishing this module from neighboring GCC modules during localization.

Rules:
- Return exactly one doc.
- Prefer stable compiler responsibilities over line-level implementation trivia.
- Do not describe a bug, test case, commit, or failure. These docs must be reusable across GCC versions.
- Do not add fields outside the schema.
"""


def build_module_doc_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYS_MODULE_DOC},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
