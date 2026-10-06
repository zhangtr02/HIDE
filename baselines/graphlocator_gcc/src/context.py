from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

from config import RuntimeConfig


@dataclass(frozen=True)
class BugRecord:
    bug_id: str
    base_commit: str


@dataclass(frozen=True)
class BugContext:
    bug_id: str
    base_commit: str
    problem_statement: str
    issue: Dict[str, Any]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for item in items:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def read_bug_records(csv_path: Path) -> List[BugRecord]:
    records: List[BugRecord] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            bug_id = str(row.get("instance_id") or "").strip()
            if not bug_id:
                continue
            records.append(
                BugRecord(
                    bug_id=bug_id,
                    base_commit=str(row.get("base_commit") or "").strip(),
                )
            )
    return records


def find_bug_record(cfg: RuntimeConfig, bug_id: str) -> BugRecord:
    for record in read_bug_records(cfg.paths.csv_file):
        if record.bug_id == bug_id:
            return record
    raise FileNotFoundError(f"bug_id not found in {cfg.paths.csv_file}: {bug_id}")


def load_issue(issue_dir: Path, bug_id: str) -> Dict[str, Any]:
    path = issue_dir / f"{bug_id}.json"
    if not path.exists():
        return {"instance_id": bug_id, "bug_report": ""}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {"instance_id": bug_id, "bug_report": ""}
    return obj if isinstance(obj, dict) else {"instance_id": bug_id, "bug_report": str(obj)}


def format_issue_labels(labels: Any) -> str:
    if labels is None:
        return ""
    if isinstance(labels, str):
        return labels.strip()
    if isinstance(labels, list):
        out: List[str] = []
        for label in labels:
            if isinstance(label, dict):
                value = str(label.get("name") or label.get("label") or "").strip()
            else:
                value = str(label or "").strip()
            if value:
                out.append(value)
        return ", ".join(out)
    return str(labels).strip()


def build_problem_statement(issue: Dict[str, Any]) -> str:
    bug_report = str(issue.get("bug_report") or "").strip()
    if bug_report:
        return f"Bug Report:\n{bug_report}"

    title = str(issue.get("title") or "").strip()
    labels = format_issue_labels(issue.get("labels"))
    body = str(issue.get("body") or "").strip()
    parts: List[str] = []
    if title:
        parts.append(f"Title: {title}")
    if labels:
        parts.append(f"Labels: {labels}")
    if body:
        parts.append(f"Body:\n{body}")
    return "\n\n".join(parts).strip()


def build_bug_context(cfg: RuntimeConfig, bug_id: str) -> BugContext:
    record = find_bug_record(cfg, bug_id)
    issue = load_issue(cfg.paths.issue_dir, bug_id)
    return BugContext(
        bug_id=bug_id,
        base_commit=record.base_commit,
        problem_statement=build_problem_statement(issue),
        issue=issue,
    )


def get_repo_head(gcc_repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(gcc_repo_dir), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    return (proc.stdout or "").strip()


def checkout_gcc_repo(gcc_repo_dir: Path, base_commit: str) -> str:
    if not base_commit:
        return get_repo_head(gcc_repo_dir)
    subprocess.run(
        ["git", "-C", str(gcc_repo_dir), "checkout", "--quiet", base_commit],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    head = get_repo_head(gcc_repo_dir)
    if not head.startswith(base_commit):
        raise RuntimeError(f"checkout verification failed: HEAD={head}, expected prefix={base_commit}")
    return head
