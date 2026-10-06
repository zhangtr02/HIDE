from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .io_utils import load_json_if_exists


def load_issue(issue_dir: Path, bug_id: str) -> Dict[str, Any]:
    obj = load_json_if_exists(issue_dir / f"{bug_id}.json")
    return obj if isinstance(obj, dict) else {"title": "", "body": "", "labels": []}


def build_problem_statement(issue: Dict[str, Any]) -> str:
    title = str(issue.get("title") or "").strip()
    labels = format_labels(issue.get("labels"))
    body = str(issue.get("body") or "").strip()
    parts: List[str] = []
    if title:
        parts.append(f"Title: {title}")
    if labels:
        parts.append(f"Labels: {labels}")
    if body:
        parts.append(f"Body:\n{body}")
    return "\n\n".join(parts).strip()


def format_labels(labels: Any) -> str:
    if labels is None:
        return ""
    if isinstance(labels, str):
        return labels.strip()
    if isinstance(labels, list):
        out: List[str] = []
        for item in labels:
            if isinstance(item, dict):
                value = str(item.get("name") or item.get("label") or "").strip()
            else:
                value = str(item or "").strip()
            if value:
                out.append(value)
        return ", ".join(out)
    return str(labels).strip()
