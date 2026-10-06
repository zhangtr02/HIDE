from __future__ import annotations

import json
from typing import Any, Dict, List


SYS_FILE_DOC = """You generate responsibility documentation for GCC source files used by compiler bug localization.

Task:
Given source files from one GCC module, generate docs only for target files that do not already have docs.

Input context:
- target_files lists the exact files that need docs in this request.
- module_id is a GCC localization module. It may be a real GCC directory such as gcc/cp or gcc/fortran, a target-specific directory such as gcc/config/i386, or a virtual root module such as gcc/root/tree-ssa or gcc/root/gimple.
- module_file_paths lists all scanned source-like files in this GCC module.
- sibling_files contains source code for the current target files.
- existing_docs contains file docs that already exist in this module and should be used as context.

Output JSON only.

Schema:
{
  "docs": [
    {
      "id": "full GCC source file path",
      "responsibility": "functional responsibility",
      "same_name_distinction": ""
    }
  ]
}

Field requirements:
- id:
  Must exactly match one target file path.
- responsibility:
  Describe this file's stable functional responsibility in GCC, in one concise but discriminative paragraph.
  Explain the compiler stage or subsystem it belongs to, what compiler data or language/target behavior it owns, and what it consumes or produces when that is visible from the code.
  Mention important data structures, public helpers, pass hooks, option tables, diagnostic paths, lowering/analysis/transformation routines, target hooks, or generated-code responsibilities when they are central to the file.
  Make the description specific enough to distinguish this file from neighboring files in the same module, especially files with similar prefixes such as trans-*, tree-*, gimple-*, ipa-*, rtl-*, i386-*, or option/header companions.
  Use the supplied source code, existing_docs, and module_file_paths as evidence.
- same_name_distinction:
  Must be "".
  Same-name distinctions are generated later by a separate Same-Name Differentiation pass.

Rules:
- Generate docs only for target_files.
- Do not generate docs for files that already have existing_docs.
- Do not modify or rewrite existing_docs.
- Every target file must have exactly one doc.
- Prefer stable compiler responsibilities over line-level implementation trivia.
- Do not describe a bug, test case, commit, or failure. These docs must be reusable across GCC versions.
- Do not add fields outside the schema.
"""


def build_file_doc_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYS_FILE_DOC},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
