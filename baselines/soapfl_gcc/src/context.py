from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from .io_utils import load_json_if_exists


@dataclass(frozen=True)
class BugContext:
    bug_id: str
    issue: Dict[str, Any]
    bug_report: str
    reproducer_code: str
    error_message: str


def build_bug_context(issue_dir: Path, bug_id: str, *, max_chars: int) -> BugContext:
    issue = load_json_if_exists(issue_dir / f"{bug_id}.json")
    if not isinstance(issue, dict):
        issue = {"instance_id": bug_id, "bug_report": ""}
    bug_report = build_bug_report(issue)[:max_chars]
    reproducer = extract_reproducer_like_text(bug_report)[:max_chars]
    return BugContext(
        bug_id=bug_id,
        issue=issue,
        bug_report=bug_report,
        reproducer_code=reproducer,
        error_message=bug_report,
    )


def build_bug_report(issue: Dict[str, Any]) -> str:
    bug_report = str(issue.get("bug_report") or "").strip()
    if bug_report:
        return bug_report
    parts: list[str] = []
    title = str(issue.get("title") or "").strip()
    labels = format_labels(issue.get("labels"))
    body = str(issue.get("body") or "").strip()
    if title:
        parts.append(f"Title: {title}")
    if labels:
        parts.append(f"Labels: {labels}")
    if body:
        parts.append(f"Body:\n{body}")
    return "\n\n".join(parts)


def extract_reproducer_like_text(text: str) -> str:
    if not text:
        return ""
    markers = ["$ cat", "cat <<", "struct ", "template", "module ", "program ", "int main", "void "]
    lower = text.lower()
    positions = [lower.find(marker.lower()) for marker in markers if lower.find(marker.lower()) >= 0]
    if not positions:
        return text
    return text[min(positions) :]


def format_labels(labels: Any) -> str:
    if labels is None:
        return ""
    if isinstance(labels, str):
        return labels.strip()
    if isinstance(labels, list):
        values: list[str] = []
        for item in labels:
            if isinstance(item, dict):
                value = str(item.get("name") or item.get("label") or "").strip()
            else:
                value = str(item or "").strip()
            if value:
                values.append(value)
        return ", ".join(values)
    return str(labels).strip()

