from __future__ import annotations

import re
import subprocess
from pathlib import Path


NIGHTLY_RE = re.compile(r"^nightly-(?P<date>\d{4}-\d{2}-\d{2})(?:-.+)?$")


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
    rustup_commit = rustup_toolchain_commit(ref)
    if rustup_commit and can_resolve_ref(repo_dir, rustup_commit):
        return rustup_commit
    nightly_commit = nightly_date_commit(repo_dir, ref)
    if nightly_commit:
        return nightly_commit
    raise RuntimeError(
        f"Cannot resolve rust git ref/toolchain: {ref}. "
        "For nightly toolchains, install the toolchain so `rustup run <toolchain> rustc -vV` "
        "can provide the exact commit hash, or ensure rust/ has enough git history for date fallback."
    )


def rustup_toolchain_commit(toolchain: str) -> str:
    result = _run(["rustup", "run", toolchain, "rustc", "-vV"])
    if result.returncode != 0:
        return ""
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() == "commit-hash":
            text = value.strip()
            if re.fullmatch(r"[0-9a-fA-F]{40}", text):
                return text
    return ""


def nightly_date_commit(repo_dir: Path, ref: str) -> str:
    match = NIGHTLY_RE.match(ref.strip())
    if not match:
        return ""
    date = match.group("date")
    result = _run(
        [
            "git",
            "log",
            "--all",
            "-n",
            "1",
            f"--before={date} 23:59:59 +0000",
            "--format=%H",
        ],
        cwd=repo_dir,
    )
    commit = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
    return commit if re.fullmatch(r"[0-9a-fA-F]{40}", commit) else ""


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
            "If this is caused by dirty or untracked rust files, rerun with --clean-checkout."
        )
