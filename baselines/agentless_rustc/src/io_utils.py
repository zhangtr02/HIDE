from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, List, Tuple


def read_rustcbugs_csv(path: Path) -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue
            bug_id = str(row[0] or "").strip()
            if not bug_id:
                continue
            toolchain = str(row[1] or "").strip() if len(row) > 1 else ""
            build_args = str(row[2] or "").strip() if len(row) > 2 else ""
            rows.append((bug_id, toolchain, build_args))
    return rows


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path) -> Any | None:
    if not path.exists():
        return None
    return load_json(path)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def unique_keep_order(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out
