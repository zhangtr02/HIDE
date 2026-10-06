from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.config import load_config
from src.common.json_io import load_json, unique_keep_order, write_json


PASS_LINE_RE = re.compile(
    r"^\s*(?P<name>[*A-Za-z0-9_.+/-][*A-Za-z0-9_.+/-]*)\s*:\s*(?P<status>ON|OFF|on|off)\b(?P<rest>.*)$"
)
LANG_BY_SUFFIX = {
    ".c": "c",
    ".i": "c",
    ".cc": "c++",
    ".cpp": "c++",
    ".cxx": "c++",
    ".C": "c++",
    ".ii": "c++",
    ".f": "fortran",
    ".f90": "fortran",
    ".f95": "fortran",
    ".f03": "fortran",
    ".f08": "fortran",
    ".F": "fortran",
    ".F90": "fortran",
}


@dataclass(frozen=True)
class TestCase:
    path: str
    content: str

    @property
    def suffix(self) -> str:
        return Path(self.path).suffix

    @property
    def language(self) -> str:
        return LANG_BY_SUFFIX.get(self.suffix, "c")


@dataclass(frozen=True)
class CompilerInvocation:
    executable: str
    prefix_args: List[str]
    source: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect GCC -fdump-passes traces for GCC CBI bugs")
    parser.add_argument("--bug-id", default="", help="Collect one GCC bug id")
    parser.add_argument("--bug-ids", default="", help="Collect comma-separated GCC bug ids")
    parser.add_argument("--limit", type=int, default=0, help="Collect first N rows from gccbugs.csv")
    parser.add_argument("--skip-existing", action="store_true", help="Skip existing usable pass_traces/<bug_id>.json")
    parser.add_argument("--gcc-bin", default="", help="Override C compiler path")
    parser.add_argument("--gxx-bin", default="", help="Override C++ compiler path")
    parser.add_argument("--gfortran-bin", default="", help="Override Fortran compiler path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    paths = cfg["paths"]
    pass_cfg = cfg.get("pass_trace") or {}
    rows = select_rows(args, read_csv(paths["dataset_csv"]))
    trace_dir: Path = paths["pass_trace_dir"]
    run_dir: Path = paths["run_dir"]
    trace_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    compilers, compiler_meta = resolve_compilers(args=args, pass_cfg=pass_cfg)
    timeout_sec = int(pass_cfg.get("timeout_sec", 120))
    extra_args = split_args(str(pass_cfg.get("extra_args") or "-c -fdump-passes"))
    default_args = {
        "c": split_args(str(pass_cfg.get("default_c_args") or "")),
        "c++": split_args(str(pass_cfg.get("default_cxx_args") or "")),
        "fortran": split_args(str(pass_cfg.get("default_fortran_args") or "")),
    }
    max_output_chars = int(pass_cfg.get("max_output_chars", 200000))

    total = len(rows)
    for index, row in enumerate(rows, start=1):
        bug_id = row["instance_id"]
        out_path = trace_dir / f"{bug_id}.json"
        if args.skip_existing and trace_is_available(out_path):
            log(f"[{index}/{total}] skip existing {bug_id}")
            continue
        if args.skip_existing and out_path.exists():
            log(f"[{index}/{total}] rerun unavailable trace {bug_id}")
        log(f"[{index}/{total}] collect {bug_id}")
        result = collect_bug_trace(
            bug_id=bug_id,
            reproducer_path=paths["reproducer_dir"] / f"{bug_id}.json",
            bug_run_dir=run_dir / bug_id,
            compilers=compilers,
            compiler_meta=compiler_meta,
            extra_args=extra_args,
            default_args=default_args,
            timeout_sec=timeout_sec,
            max_output_chars=max_output_chars,
        )
        write_json(out_path, result)


def collect_bug_trace(
    *,
    bug_id: str,
    reproducer_path: Path,
    bug_run_dir: Path,
    compilers: Dict[str, CompilerInvocation],
    compiler_meta: Dict[str, Any],
    extra_args: List[str],
    default_args: Dict[str, List[str]],
    timeout_sec: int,
    max_output_chars: int,
) -> Dict[str, Any]:
    if bug_run_dir.exists():
        shutil.rmtree(bug_run_dir)
    bug_run_dir.mkdir(parents=True, exist_ok=True)
    cases = load_test_cases(reproducer_path)
    case_results: List[Dict[str, Any]] = []
    all_passes: List[Dict[str, Any]] = []
    languages: List[str] = []
    options: List[str] = []
    failed_cases: List[str] = []

    for case_index, case in enumerate(cases, start=1):
        case_path = write_case(bug_run_dir, case_index, case)
        languages.append(case.language)
        compiler = compilers.get(case.language)
        if not compiler:
            case_results.append(
                {
                    "path": case.path,
                    "written_path": str(case_path.relative_to(bug_run_dir)),
                    "language": case.language,
                    "status": "missing_compiler",
                }
            )
            failed_cases.append(case.path)
            continue
        dejagnu_options, option_warnings = extract_dg_options(case.content)
        inferred_options = infer_case_options(case)
        case_options = normalize_options([*default_args.get(case.language, []), *inferred_options, *dejagnu_options, *extra_args])
        object_path = bug_run_dir / "_objects" / f"case_{case_index}.o"
        object_path.parent.mkdir(parents=True, exist_ok=True)
        args = [compiler.executable, *compiler.prefix_args, *case_options, "-o", str(object_path), str(case_path)]
        options.extend(arg for arg in args[1:] if arg.startswith("-"))
        start = time.time()
        try:
            completed = subprocess.run(
                args,
                cwd=bug_run_dir,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_sec,
                check=False,
            )
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            completed = None
            timed_out = True
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        else:
            stdout = completed.stdout
            stderr = completed.stderr
        elapsed = time.time() - start
        raw_output = (stdout or "") + "\n" + (stderr or "")
        passes = parse_fdump_passes(raw_output)
        for item in passes:
            item["case_path"] = case.path
            item["language"] = case.language
        all_passes.extend(passes)
        if timed_out or (completed is not None and completed.returncode != 0):
            failed_cases.append(case.path)
        case_results.append(
            {
                "path": case.path,
                "written_path": str(case_path.relative_to(bug_run_dir)),
                "language": case.language,
                "command": redact_command(args),
                "compiler_source": compiler.source,
                "inferred_options": inferred_options,
                "dejagnu_options": dejagnu_options,
                "option_warnings": option_warnings,
                "return_code": None if completed is None else completed.returncode,
                "timed_out": timed_out,
                "elapsed_sec": round(elapsed, 3),
                "pass_count": len(passes),
                "output_excerpt": raw_output[:max_output_chars],
            }
        )

    unique_passes = unique_pass_items(all_passes)
    return {
        "instance_id": bug_id,
        "source": "fdump-passes",
        "available": bool(unique_passes),
        "compiler": compiler_meta,
        "case_count": len(cases),
        "languages": unique_keep_order(languages),
        "options": unique_keep_order(options),
        "passes": unique_passes,
        "stage_counts": stage_counts(unique_passes),
        "failed_cases": unique_keep_order(failed_cases),
        "cases": case_results,
    }


def parse_fdump_passes(output: str) -> List[Dict[str, Any]]:
    passes: List[Dict[str, Any]] = []
    for line in output.splitlines():
        match = PASS_LINE_RE.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        status = match.group("status").upper()
        if status != "ON":
            continue
        passes.append(
            {
                "name": name,
                "kind": infer_pass_kind(name),
                "stage": infer_pass_stage(name=name),
                "enabled": True,
                **parse_pass_rest(match.group("rest")),
            }
        )
    return passes


def parse_pass_rest(rest: str) -> Dict[str, Any]:
    item: Dict[str, Any] = {}
    for key, label in [
        ("properties_required", "properties_required:"),
        ("properties_provided", "properties_provided:"),
        ("todo_flags_start", "todo_flags_start:"),
        ("todo_flags_finish", "todo_flags_finish:"),
    ]:
        if label not in rest:
            continue
        tail = rest.split(label, 1)[1]
        for stop in [" properties_", " todo_flags_"]:
            if stop in tail:
                tail = tail.split(stop, 1)[0]
        value = " ".join(tail.strip().split())
        if value:
            item[key] = value
    return item


def infer_pass_kind(name: str) -> str:
    clean = name.lstrip("*")
    if "-" in clean:
        return clean.split("-", 1)[0]
    if "_" in clean:
        return clean.split("_", 1)[0]
    return "pass"


def infer_pass_stage(*, name: str) -> str:
    text = name.lower().lstrip("*")
    if text.startswith("ipa-") or text == "ipa" or "ipa" in text:
        return "ipa"
    if text.startswith("rtl-") or "rtl" in text or any(token in text for token in ["reload", "combine", "sched", "ira", "lra"]):
        return "rtl"
    if text.startswith("tree-") or "tree" in text or any(token in text for token in ["gimple", "vrp", "evrp", "dom", "ccp", "dce", "fre", "thread"]):
        return "tree"
    if "lower" in text or "cfg" in text:
        return "lowering"
    return "other"


def unique_pass_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in items:
        key = (str(item.get("name") or ""), str(item.get("kind") or ""), str(item.get("stage") or ""))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append({k: v for k, v in item.items() if k not in {"case_path", "language"}})
    return out


def stage_counts(passes: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in passes:
        stage = str(item.get("stage") or "other")
        counts[stage] = counts.get(stage, 0) + 1
    return dict(sorted(counts.items()))


def load_test_cases(path: Path) -> List[TestCase]:
    obj = load_json(path)
    cases: List[TestCase] = []
    for item in obj.get("test_cases") or []:
        record = item if isinstance(item, dict) else {}
        case_path = str(record.get("path") or f"case{len(cases) + 1}.c").strip()
        content = str(record.get("content") or "")
        cases.append(TestCase(path=case_path, content=content))
    return cases


def write_case(root: Path, index: int, case: TestCase) -> Path:
    rel = Path(case.path)
    if rel.is_absolute() or ".." in rel.parts:
        rel = Path(f"case_{index}{case.suffix or '.c'}")
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(case.content, encoding="utf-8", errors="replace")
    return path


def extract_dg_options(content: str) -> tuple[List[str], List[str]]:
    options: List[str] = []
    warnings: List[str] = []
    for match in re.finditer(r"dg-(?:additional-)?options\s+\"([^\"]*)\"", content):
        raw = match.group(1)
        parsed, warning = split_args_safe(raw)
        options.extend(parsed)
        if warning:
            warnings.append(warning)
    return options, warnings


def infer_case_options(case: TestCase) -> List[str]:
    if has_std_option(case.content):
        return []
    if case.language == "c++":
        std = infer_cpp_std(case)
        return [std] if std else []
    if case.language == "c":
        std = infer_c_std(case)
        return [std] if std else []
    return []


def infer_cpp_std(case: TestCase) -> str:
    text = f"{case.path}\n{case.content}".lower()
    if "c++26" in text or "cpp26" in text or "cpp2c" in text:
        return "-std=c++2c"
    if "c++23" in text or "cpp23" in text or "cpp2b" in text:
        return "-std=c++23"
    if "c++20" in text or "cpp20" in text or "cpp2a" in text or "coroutines" in text or "concept" in text:
        return "-std=c++20"
    if "c++17" in text or "cpp17" in text or "cpp1z" in text:
        return "-std=c++17"
    if "c++14" in text or "cpp14" in text or "cpp1y" in text:
        return "-std=c++14"
    if "c++11" in text or "cpp11" in text or "cpp0x" in text:
        return "-std=c++11"
    return ""


def infer_c_std(case: TestCase) -> str:
    text = f"{case.path}\n{case.content}".lower()
    if "c23" in text or "c2x" in text:
        return "-std=c2x"
    if "c11" in text or "c1x" in text:
        return "-std=c11"
    if "c99" in text:
        return "-std=c99"
    return ""


def has_std_option(text: str) -> bool:
    return "-std=" in text


def normalize_options(options: List[str]) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    for option in options:
        if option == "-std=c23":
            option = "-std=c2x"
        elif option == "-std=gnu23":
            option = "-std=gnu2x"
        if option in seen:
            continue
        seen.add(option)
        normalized.append(option)
    return normalized


def split_args(text: str) -> List[str]:
    import shlex

    return shlex.split(text) if text.strip() else []


def split_args_safe(text: str) -> tuple[List[str], str]:
    if not text.strip():
        return [], ""
    try:
        return split_args(text), ""
    except ValueError as exc:
        repaired = text.rstrip()
        if repaired.endswith("\\"):
            repaired = repaired[:-1]
        try:
            return split_args(repaired), f"repaired malformed dg-options {text!r}: {exc}"
        except ValueError:
            fallback = [part for part in re.split(r"\s+", repaired) if part]
            return fallback, f"fallback split malformed dg-options {text!r}: {exc}"


def resolve_compilers(*, args: argparse.Namespace, pass_cfg: Dict[str, Any]) -> tuple[Dict[str, CompilerInvocation], Dict[str, Any]]:
    configured = {
        "c": str(args.gcc_bin or pass_cfg.get("gcc_bin") or "").strip(),
        "c++": str(args.gxx_bin or pass_cfg.get("gxx_bin") or "").strip(),
        "fortran": str(args.gfortran_bin or pass_cfg.get("gfortran_bin") or "").strip(),
    }
    defaults = {"c": "gcc", "c++": "g++", "fortran": "gfortran"}
    compilers: Dict[str, CompilerInvocation] = {}
    for language, default_name in defaults.items():
        configured_path = configured[language]
        executable = configured_path or resolve_system_compiler(default_name)
        if not executable:
            continue
        compilers[language] = CompilerInvocation(
            executable=executable,
            prefix_args=[],
            source="explicit" if configured_path else "system",
        )
    return compilers, {
        "mode": "system",
        "note": "using installed GCC compilers from PATH unless overridden",
        "commands": compiler_metadata(compilers),
    }


def compiler_metadata(compilers: Dict[str, CompilerInvocation]) -> Dict[str, Dict[str, Any]]:
    return {
        language: {
            "executable": compiler.executable,
            "prefix_args": compiler.prefix_args,
            "source": compiler.source,
        }
        for language, compiler in compilers.items()
    }


def resolve_system_compiler(name: str) -> str:
    return shutil.which(name) or ""


def trace_is_available(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        obj = load_json(path)
    except Exception:
        return False
    passes = obj.get("passes") if isinstance(obj.get("passes"), list) else []
    return bool(obj.get("available")) and bool(passes)


def read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def select_rows(args: argparse.Namespace, rows: List[Dict[str, str]]) -> List[Dict[str, str]]:
    by_id = {row.get("instance_id", ""): row for row in rows}
    requested: List[str] = []
    if args.bug_id.strip():
        requested.append(args.bug_id.strip())
    if args.bug_ids.strip():
        requested.extend(item.strip() for item in args.bug_ids.split(",") if item.strip())
    requested = unique_keep_order(requested)
    if requested:
        missing = [bug_id for bug_id in requested if bug_id not in by_id]
        if missing:
            raise RuntimeError(f"bug_id not found in gccbugs.csv: {', '.join(missing)}")
        return [by_id[bug_id] for bug_id in requested]
    return rows[: args.limit] if args.limit else rows


def redact_command(args: List[str]) -> List[str]:
    return [str(item) for item in args]


def log(message: str) -> None:
    print(message, flush=True)


if __name__ == "__main__":
    main()
