from __future__ import annotations

import subprocess
from pathlib import Path


def checkout_ref(gcc_repo_dir: Path, ref: str) -> str:
    if not ref:
        return get_head(gcc_repo_dir)
    subprocess.run(
        ["git", "-C", str(gcc_repo_dir), "checkout", "--quiet", ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    return get_head(gcc_repo_dir)


def get_head(gcc_repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(gcc_repo_dir), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    return (proc.stdout or "").strip()
