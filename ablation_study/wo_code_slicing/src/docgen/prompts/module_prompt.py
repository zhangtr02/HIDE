from __future__ import annotations

import json
from typing import Any, Dict, List


SYS_MODULE_DOC = """You generate responsibility documentation for one rustc crate or module used by compiler bug localization.

Task:
Given the file list and available file docs for one rustc localization crate or module, generate one doc for target_module.

Input context:
- target_module is either a rustc compiler crate such as rustc_hir_typeck or a module such as rustc_hir_typeck::fn_ctxt.
- module_file_paths lists all accumulated Rust file docs currently known for this crate or module across prepared rustc commits.
- file_docs contains reusable responsibility docs for files in this crate or module when available.

Output JSON only.

Schema:
{
  "docs": [
    {
      "id": "rustc crate or crate::module id",
      "responsibility": "module-level functional responsibility"
    }
  ]
}

Field requirements:
- id:
  Must exactly match target_module.
- responsibility:
  Describe this crate or module's stable functional responsibility in rustc, in one coherent paragraph.
  Explain the compiler stage or subsystem represented by this crate/module and how its main file groups contribute to that role.
  Use file_docs first when available, then module id and file paths as supporting evidence.
  Cover the major functionality represented by the crate/module without listing every file one by one.
  Mention important subareas, recurring filename families, major compiler concepts, Rust language rules, query behavior, diagnostics, lowering, type checking, trait solving, borrow checking, MIR analysis, transformation, emission, or support logic when they define the crate/module.
  Make the description useful for distinguishing this crate/module from neighboring rustc crates/modules during localization.

Rules:
- Return exactly one doc.
- Prefer stable compiler responsibilities over line-level implementation trivia.
- Do not describe a bug, test case, commit, or failure. These docs must be reusable across rustc versions.
- Do not add fields outside the schema.
"""


def build_module_doc_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYS_MODULE_DOC},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
