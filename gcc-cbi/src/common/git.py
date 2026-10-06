from __future__ import annotations

import subprocess
from pathlib import Path


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def can_resolve_ref(repo_dir: Path, ref: str) -> bool:
    result = _run(["git", "rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo_dir)
    return result.returncode == 0


def resolve_checkout_ref(repo_dir: Path, ref: str) -> str:
    ref = ref.strip()
    if not ref:
        raise RuntimeError("empty git ref")
    if can_resolve_ref(repo_dir, ref):
        return ref
    raise RuntimeError(f"Cannot resolve GCC git ref: {ref}")


def clean_worktree(repo_dir: Path) -> None:
    reset = _run(["git", "reset", "--hard", "--quiet"], cwd=repo_dir)
    if reset.returncode != 0:
        raise RuntimeError(f"git reset failed: {reset.stderr.strip()}")
    clean = _run(["git", "clean", "-ffdx", "--quiet"], cwd=repo_dir)
    if clean.returncode != 0:
        raise RuntimeError(f"git clean failed: {clean.stderr.strip()}")


def checkout_ref(repo_dir: Path, ref: str, *, force: bool = False) -> None:
    if not ref:
        raise RuntimeError("empty git ref")
    cmd = ["git", "checkout", "--quiet", ref]
    if force:
        cmd = ["git", "checkout", "--force", "--quiet", ref]
    result = _run(cmd, cwd=repo_dir)
    if result.returncode != 0:
        raise RuntimeError(
            f"git checkout failed for {ref}: {result.stderr.strip()}\n"
            "If this is caused by dirty or untracked GCC files, rerun with --clean-checkout."
        )
