from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.evidence.extractor import extract_issue, extract_reproducer


def load_issue(issue_dir: Path, bug_id: str, *, max_chars: int = 24000) -> Dict[str, Any]:
    return extract_issue(issue_dir, bug_id, max_chars=max_chars)


def load_reproducer(reproducer_dir: Path, bug_id: str, *, max_chars: int = 24000) -> Dict[str, Any]:
    return extract_reproducer(reproducer_dir, bug_id, max_chars=max_chars)
