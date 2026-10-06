from __future__ import annotations

import json
from typing import Any, Dict, List


SYS_SAME_NAME_DIFFERENTIATION = """You add Same-Name Differentiation to GCC file docs.

Task:
Given file docs and source code for files in one GCC module that share the same filename stem, generate updates only for target_files that need same_name_distinction.
A shared filename stem ignores the extension, so files such as foo.c, foo.cc, and foo.h belong to the same group.
For very large same-name groups, this request may include only a target subset of the group.

Output JSON only.

Schema:
{
  "updates": [
    {
      "id": "full GCC source file path",
      "same_name_distinction": "same-name file distinction"
    }
  ]
}

Field requirements:
- id:
  Must exactly match one file in target_files.
- same_name_distinction:
  Explain how this file differs from the other files with the same filename stem in this module.
  Use file responsibility, source code, file path, and same_name_group_all_paths as evidence.
  Do not restate the whole responsibility.
  Focus only on the distinction caused by sharing the same filename stem.

Rules:
- Generate updates only for target_files in this request.
- Do not modify responsibility.
- Every target file must have exactly one update.
- Prefer stable compiler responsibilities over line-level implementation trivia.
"""


def build_same_name_messages(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"role": "system", "content": SYS_SAME_NAME_DIFFERENTIATION},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
