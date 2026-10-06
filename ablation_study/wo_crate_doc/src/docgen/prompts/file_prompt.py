from __future__ import annotations

import json
from typing import Any, Dict, List


SYS_FILE_DOC = """You generate responsibility documentation for rustc compiler source files.

Task:
Given source files from one rustc compiler crate or module, generate docs only for target files that do not already have docs.

Input context:
- target_files lists the exact files that need docs in this request.
- module_id is a rustc localization module such as rustc_hir_typeck::fn_ctxt, or a crate-root pseudo module ending in ::__root__.
- module_file_paths lists all scanned Rust files in this module or crate-root group.
- sibling_files contains source code for the current target files.
- existing_docs contains file docs that already exist in this module and should be used as context.

Output JSON only.

Schema:
{
  "docs": [
    {
      "id": "full rustc compiler source file path",
      "responsibility": "functional responsibility",
      "same_name_distinction": ""
    }
  ]
}

Field requirements:
- id:
  Must exactly match one target file path.
- responsibility:
  Describe this file's stable functional responsibility in rustc.
  Explain what compiler data, Rust language rules, query behavior, diagnostics, lowering, type checking, trait solving, borrow checking, MIR analysis, transformation, or support logic the file owns.
  Use the supplied source code, existing_docs, and module_file_paths to make the responsibility specific within this module.
- same_name_distinction:
  Must be "".
  Same-name distinctions are generated later by a separate Same-Name Differentiation pass.

Rules:
- Generate docs only for target_files.
- Do not generate docs for files that already have existing_docs.
- Do not modify or rewrite existing_docs.
- Every target file must have exactly one doc.
- Prefer stable compiler responsibilities over line-level implementation trivia.
"""


def build_file_doc_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYS_FILE_DOC},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
