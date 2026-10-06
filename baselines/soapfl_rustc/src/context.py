from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .config import RuntimeConfig


@dataclass(frozen=True)
class BugContext:
    bug_id: str
    toolchain: str
    build_args: str
    reproducer_code: str
    error_message: str


def build_bug_context(
    cfg: RuntimeConfig,
    *,
    bug_id: str,
    toolchain: str,
    build_args: str,
    build_if_log_missing: bool = False,
) -> BugContext:
    reproducer_code = collect_reproducer_code(cfg, bug_id)
    error_message = load_error_message(cfg, bug_id)
    if not error_message and build_if_log_missing:
        error_message = build_bug_reproducer(cfg, bug_id=bug_id, toolchain=toolchain, build_args=build_args)
    return BugContext(
        bug_id=bug_id,
        toolchain=toolchain,
        build_args=build_args,
        reproducer_code=reproducer_code,
        error_message=error_message,
    )


def collect_reproducer_code(cfg: RuntimeConfig, bug_id: str) -> str:
    crate_dir = cfg.paths.dataset_dir / bug_id
    if not crate_dir.exists():
        crate_dir = cfg.paths.run_dir / bug_id
    if not crate_dir.exists():
        return ""

    parts: List[str] = []
    for rel, limit in [
        ("Cargo.toml", 6000),
        ("rust-toolchain.toml", 2000),
        ("src/main.rs", 18000),
        ("src/lib.rs", 18000),
    ]:
        path = crate_dir / rel
        if path.exists():
            parts.append(f"=== {rel} ===\n{read_text(path, limit)}")

    if not parts:
        for path in list(crate_dir.rglob("*.rs"))[:6]:
            parts.append(f"=== {path.relative_to(crate_dir).as_posix()} ===\n{read_text(path, 8000)}")
    return "\n\n".join(parts)[: cfg.soapfl.reproducer_chars]


def load_error_message(cfg: RuntimeConfig, bug_id: str) -> str:
    log_file = cfg.paths.log_dir / f"{bug_id}.log"
    if not log_file.exists():
        return ""
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    if len(text) <= cfg.soapfl.error_chars:
        return text
    return text[-cfg.soapfl.error_chars :].strip()


def read_text(path: Path, limit: int) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def parse_build_args(build_args: str) -> tuple[List[str], str]:
    cargo_args: List[str] = []
    rustflags = ""
    for item in shlex.split(build_args or ""):
        if item.startswith("RUSTFLAGS="):
            rustflags = item.split("=", 1)[1]
        else:
            cargo_args.append(item)
    return cargo_args, rustflags


def build_bug_reproducer(cfg: RuntimeConfig, *, bug_id: str, toolchain: str, build_args: str) -> str:
    src_dir = cfg.paths.dataset_dir / bug_id
    run_dir = cfg.paths.run_dir / bug_id
    if not src_dir.exists():
        return ""
    if not run_dir.exists():
        shutil.copytree(src_dir, run_dir)

    cargo_args, rustflags = parse_build_args(build_args)
    env = os.environ.copy()
    env["RUST_BACKTRACE"] = "full"
    if rustflags:
        env["RUSTFLAGS"] = (env.get("RUSTFLAGS", "") + " " + rustflags).strip()

    cmd = ["rustup", "run", toolchain, "cargo", "build", *cargo_args]
    proc = subprocess.run(
        cmd,
        cwd=run_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = proc.stdout or ""
    log_text = "\n".join(
        [
            f"Bug ID: {bug_id}",
            f"Toolchain: {toolchain}",
            f"CMD: {shlex.join(cmd)}",
            "-" * 60,
            output.rstrip("\n"),
            "-" * 60,
            f"Return code: {proc.returncode}",
            "",
        ]
    )
    cfg.paths.log_dir.mkdir(parents=True, exist_ok=True)
    (cfg.paths.log_dir / f"{bug_id}.log").write_text(log_text, encoding="utf-8")
    return output
