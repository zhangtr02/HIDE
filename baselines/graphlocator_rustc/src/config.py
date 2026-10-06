from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

try:
    import yaml
except Exception:
    yaml = None


@dataclass(frozen=True)
class PathsConfig:
    root: Path
    issue_dir: Path
    groundtruth_dir: Path
    method_groundtruth_dir: Path
    rust_repo_dir: Path
    csv_file: Path


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    temperature: float
    timeout_sec: int
    max_completion_tokens: int = 8192


@dataclass(frozen=True)
class GraphLocatorConfig:
    run_method_level: bool = True
    search_topk: int = 5
    max_search_turn: int = 5
    max_causal_turn: int = 20
    file_top_n: int = 10
    method_top_n: int = 10
    max_causal_code_elements: int = 80
    max_causal_node_content_chars: int = 4000
    max_existing_node_content_chars: int = 1200
    max_causal_prompt_chars: int = 350000


@dataclass(frozen=True)
class RuntimeConfig:
    paths: PathsConfig
    llm: LLMConfig
    graphlocator: GraphLocatorConfig
    report_dir: Path
    graph_cache_dir: Path


def load_runtime_config(
    *,
    root: Path | None = None,
    report_dir: Path | None = None,
    graph_cache_dir: Path | None = None,
) -> RuntimeConfig:
    baseline_root = find_baseline_root(Path(__file__).resolve())
    config_path = baseline_root / "config" / "config.yaml"
    raw = load_yaml_mapping(config_path)

    paths = raw.get("paths") or {}
    llm = raw.get("llm") or {}
    graphlocator = raw.get("graphlocator") or {}
    project_root = (root or (baseline_root / Path(str(paths.get("project_root") or "..")))).resolve()

    path_cfg = PathsConfig(
        root=project_root,
        issue_dir=resolve_path(project_root, paths["issue_dir"]),
        groundtruth_dir=resolve_path(project_root, paths["groundtruth_dir"]),
        method_groundtruth_dir=resolve_path(project_root, paths.get("method_groundtruth_dir", "method_groundtruth")),
        rust_repo_dir=resolve_path(project_root, paths["rust_repo_dir"]),
        csv_file=resolve_path(project_root, paths["csv_file"]),
    )
    llm_cfg = LLMConfig(
        base_url=str(llm.get("base_url") or "https://api.openai.com/v1"),
        model=str(llm.get("model") or "gpt-4o-2024-11-20"),
        temperature=float(llm.get("temperature", 0.0)),
        timeout_sec=int(llm.get("timeout_sec", 120)),
        # GraphLocator's original implementation uses 8192 for agent responses.
        max_completion_tokens=int(llm.get("max_completion_tokens") or llm.get("max_tokens") or 8192),
    )
    return RuntimeConfig(
        paths=path_cfg,
        llm=llm_cfg,
        graphlocator=GraphLocatorConfig(
            run_method_level=bool(graphlocator.get("run_method_level", True)),
            search_topk=int(graphlocator.get("search_topk", 5)),
            max_search_turn=int(graphlocator.get("max_search_turn", 5)),
            max_causal_turn=int(graphlocator.get("max_causal_turn", 20)),
            file_top_n=int(graphlocator.get("file_top_n", graphlocator.get("top_files", 10))),
            method_top_n=int(graphlocator.get("method_top_n", 10)),
            max_causal_code_elements=int(graphlocator.get("max_causal_code_elements", 80)),
            max_causal_node_content_chars=int(graphlocator.get("max_causal_node_content_chars", 4000)),
            max_existing_node_content_chars=int(graphlocator.get("max_existing_node_content_chars", 1200)),
            max_causal_prompt_chars=int(graphlocator.get("max_causal_prompt_chars", 350000)),
        ),
        report_dir=(report_dir or resolve_path(baseline_root, paths.get("report_dir", "reports"))).resolve(),
        graph_cache_dir=(graph_cache_dir or resolve_path(baseline_root, paths.get("graph_cache_dir", "graph_cache"))).resolve(),
    )


def find_baseline_root(start: Path) -> Path:
    for parent in [start.parent, *start.parents]:
        if (parent / "config" / "config.yaml").exists() and (parent / "src").exists():
            return parent
    raise FileNotFoundError("Cannot locate graphlocator_rustc root containing config/config.yaml and src/")


def resolve_path(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def load_yaml_mapping(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}

    result: Dict[str, Any] = {}
    current_section: Dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not raw_line.startswith((" ", "\t")) and line.endswith(":"):
            section_name = line[:-1].strip()
            current_section = {}
            result[section_name] = current_section
            continue
        if current_section is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_section[key.strip()] = parse_scalar(value.strip())
    return result


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
