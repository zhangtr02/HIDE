from __future__ import annotations

import subprocess
from pathlib import Path


def get_rustc_commit_hash(toolchain: str) -> str:
    proc = subprocess.run(
        ["rustup", "run", toolchain, "rustc", "-Vv"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    for line in (proc.stdout or "").splitlines():
        if line.lower().startswith("commit-hash:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"Cannot find commit-hash for toolchain {toolchain}")


def checkout_ref(rust_repo_dir: Path, toolchain: str) -> str:
    commit_hash = get_rustc_commit_hash(toolchain)
    subprocess.run(
        ["git", "-C", str(rust_repo_dir), "checkout", "--quiet", commit_hash],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    return get_head(rust_repo_dir)


def get_head(rust_repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(rust_repo_dir), "rev-parse", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=True,
    )
    return (proc.stdout or "").strip()
