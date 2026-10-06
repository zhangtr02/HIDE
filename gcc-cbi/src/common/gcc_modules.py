from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, List


SKIP_DIRS = {".git", "__pycache__"}
SKIP_TOP_LEVEL_MODULES = {"testsuite"}
SPLIT_TOP_LEVEL_MODULES = {"config", "common", "m2", "rust", "d", "go", "ada"}


def enumerate_virtual_modules(gcc_repo_dir: Path, *, source_suffixes: set[str]) -> List[str]:
    gcc_dir = gcc_repo_dir / "gcc"
    if not gcc_dir.is_dir():
        return []

    modules: List[str] = []
    modules.extend(root_module_id(path.name) for path in immediate_source_files(gcc_dir, source_suffixes))

    for path in sorted(gcc_dir.iterdir()):
        if not path.is_dir() or path.name in SKIP_TOP_LEVEL_MODULES:
            continue
        if path.name in SPLIT_TOP_LEVEL_MODULES:
            modules.extend(split_directory_modules(gcc_repo_dir, path, source_suffixes=source_suffixes))
        elif has_source_file(path, source_suffixes=source_suffixes):
            modules.append(path.relative_to(gcc_repo_dir).as_posix())
    return unique(modules)


def enumerate_files_for_module(gcc_repo_dir: Path, module_id: str, *, source_suffixes: set[str]) -> List[str]:
    gcc_dir = gcc_repo_dir / "gcc"
    module_id = str(module_id or "").strip()
    if not module_id:
        return []

    if module_id == "gcc/root":
        return [path.relative_to(gcc_repo_dir).as_posix() for path in immediate_source_files(gcc_dir, source_suffixes)]

    if module_id.startswith("gcc/root/"):
        return [
            path.relative_to(gcc_repo_dir).as_posix()
            for path in immediate_source_files(gcc_dir, source_suffixes)
            if root_module_id(path.name) == module_id
        ]

    if module_id.endswith("/root"):
        base_dir = gcc_repo_dir / module_id[: -len("/root")]
        return [path.relative_to(gcc_repo_dir).as_posix() for path in immediate_source_files(base_dir, source_suffixes)]

    module_dir = gcc_repo_dir / module_id
    if not module_dir.is_dir():
        return []
    return [
        path.relative_to(gcc_repo_dir).as_posix()
        for path in sorted(module_dir.rglob("*"))
        if is_source_file(path, source_suffixes=source_suffixes) and not is_skipped(path)
    ]


def module_abs_path(gcc_repo_dir: Path, module_id: str) -> Path:
    if module_id.startswith("gcc/root/"):
        return gcc_repo_dir / "gcc"
    if module_id.endswith("/root"):
        return gcc_repo_dir / module_id[: -len("/root")]
    return gcc_repo_dir / module_id


def split_directory_modules(gcc_repo_dir: Path, module_dir: Path, *, source_suffixes: set[str]) -> List[str]:
    modules: List[str] = []
    base_id = module_dir.relative_to(gcc_repo_dir).as_posix()
    if immediate_source_files(module_dir, source_suffixes):
        modules.append(f"{base_id}/root")
    for child in sorted(module_dir.iterdir()):
        if child.is_dir() and has_source_file(child, source_suffixes=source_suffixes):
            modules.append(child.relative_to(gcc_repo_dir).as_posix())
    return modules


def immediate_source_files(directory: Path, source_suffixes: set[str]) -> List[Path]:
    if not directory.is_dir():
        return []
    return [
        path
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix in source_suffixes and not is_skipped(path)
    ]


def has_source_file(path: Path, *, source_suffixes: set[str]) -> bool:
    for child in path.rglob("*"):
        if is_source_file(child, source_suffixes=source_suffixes) and not is_skipped(child):
            return True
    return False


def is_source_file(path: Path, *, source_suffixes: set[str]) -> bool:
    return path.is_file() and path.suffix in source_suffixes


def is_skipped(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & SKIP_DIRS) or "testsuite" in parts


def root_module_id(filename: str) -> str:
    rules: list[tuple[str, Callable[[str], bool]]] = [
        ("gcc/root/tree-ssa", lambda n: n.startswith("tree-ssa")),
        ("gcc/root/tree-vect", lambda n: n.startswith("tree-vect")),
        ("gcc/root/tree-core", lambda n: n.startswith("tree")),
        ("gcc/root/gimple", lambda n: n.startswith("gimple") or n.startswith("gimplify")),
        ("gcc/root/ipa-cgraph", lambda n: n.startswith(("ipa", "cgraph", "symtab"))),
        (
            "gcc/root/cfg",
            lambda n: n.startswith(("cfg", "cfgrtl", "cfgbuild", "dominance", "domwalk", "et-forest", "basic-block", "bb-")),
        ),
        ("gcc/root/rtl-core", lambda n: n.startswith(("rtl", "df", "ddg", "recog", "insn", "regstat"))),
        ("gcc/root/regalloc-sched", lambda n: n.startswith(("ira", "lra", "reload", "reg", "regrename", "sel-sched", "sched", "haifa"))),
        ("gcc/root/rtl-opts", lambda n: n.startswith(("combine", "cse", "cselib", "fwprop", "gcse", "cprop", "dce", "dse", "ree", "auto-inc-dec", "compare-elim", "store-motion"))),
        ("gcc/root/loop-opts", lambda n: n.startswith(("loop", "graphite", "modulo-sched"))),
        ("gcc/root/fold-range", lambda n: n.startswith(("fold", "match", "value-range", "range", "vr", "wide-int", "real", "fixed-value", "double-int", "poly-int", "signop"))),
        ("gcc/root/expand-emit", lambda n: n.startswith(("expr", "explow", "expmed", "expand", "stmt", "dojump", "calls", "builtins", "builtin", "optabs", "emit-rtl", "varasm", "final", "output"))),
        ("gcc/root/diagnostics", lambda n: n.startswith(("diagnostic", "pretty-print", "rich-location", "input", "errors", "spellcheck", "gcc-rich-location", "gcc-diagnostic", "gcc-urlifier", "libgdiagnostics"))),
        ("gcc/root/options", lambda n: n.startswith(("opts", "options", "opt-")) or n.endswith(".opt") or n in {"common.opt", "params.opt"}),
        ("gcc/root/openmp-openacc", lambda n: n.startswith(("omp", "oacc", "gomp"))),
        ("gcc/root/sanitizers", lambda n: "san" in n or n.startswith(("asan", "ubsan", "tsan"))),
        ("gcc/root/profile-coverage", lambda n: n.startswith(("profile", "coverage", "gcov", "auto-profile", "value-prof"))),
        ("gcc/root/lto", lambda n: n.startswith(("lto", "ltrans"))),
        ("gcc/root/dump-debug", lambda n: n.startswith(("dump", "timevar", "statistics", "dbgcnt", "optinfo", "json", "graph", "graphviz"))),
        ("gcc/root/generators", lambda n: n.startswith(("gen", "read-md", "gensupport"))),
        ("gcc/root/infra-containers", lambda n: n.startswith(("ggc", "alloc-pool", "bitmap", "hash", "vec", "splay", "obstack", "fibonacci", "typed-splay"))),
        ("gcc/root/hooks-attrs", lambda n: n.startswith(("target", "targhooks", "hooks", "hosthooks", "langhooks", "plugin", "attribs", "attr-"))),
        ("gcc/root/driver", lambda n: n.startswith(("gcc", "collect2", "collect-utils", "driver", "file-prefix-map", "prefix", "incpath", "cpp"))),
        ("gcc/root/debug-info", lambda n: n.startswith(("dwarf", "dbxout", "xcoffout", "ctf", "btf", "debug"))),
        ("gcc/root/middle-end-misc", lambda n: n.startswith(("except", "function", "context", "backend", "alias", "convert", "stor-layout"))),
    ]
    for module_id, predicate in rules:
        if predicate(filename):
            return module_id
    return "gcc/root/misc"


def unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
