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
    gcc_repo_dir: Path
    csv_file: Path
    issue_dir: Path
    groundtruth_dir: Path
    method_groundtruth_dir: Path
    report_dir: Path


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    temperature: float
    timeout_sec: int
    max_tokens: int


@dataclass(frozen=True)
class AgentlessConfig:
    file_top_n: int
    related_top_n: int
    method_top_k: int
    max_line_context_chars: int


@dataclass(frozen=True)
class RuntimeConfig:
    paths: PathsConfig
    llm: LLMConfig
    agentless: AgentlessConfig


def load_config() -> RuntimeConfig:
    root = Path(__file__).resolve().parents[1]
    config_path = root / "config" / "config.yaml"
    raw = load_yaml(config_path)
    paths = raw.get("paths") or {}
    llm = raw.get("llm") or {}
    agentless = raw.get("agentless") or {}

    return RuntimeConfig(
        paths=PathsConfig(
            root=root,
            gcc_repo_dir=resolve(root, paths["gcc_repo_dir"]),
            csv_file=resolve(root, paths["csv_file"]),
            issue_dir=resolve(root, paths["issue_dir"]),
            groundtruth_dir=resolve(root, paths["groundtruth_dir"]),
            method_groundtruth_dir=resolve(root, paths["method_groundtruth_dir"]),
            report_dir=resolve(root, paths.get("report_dir", "reports")),
        ),
        llm=LLMConfig(
            base_url=str(llm.get("base_url") or "https://api.openai.com/v1"),
            model=str(llm.get("model") or "gpt-5.4"),
            temperature=float(llm.get("temperature", 0.0)),
            timeout_sec=int(llm.get("timeout_sec", 120)),
            max_tokens=int(llm.get("max_tokens", 1600)),
        ),
        agentless=AgentlessConfig(
            file_top_n=int(agentless.get("file_top_n", 10)),
            related_top_n=int(agentless.get("related_top_n", 5)),
            method_top_k=int(agentless.get("method_top_k", 10)),
            max_line_context_chars=int(agentless.get("max_line_context_chars", 60000)),
        ),
    )


def resolve(root: Path, value: str | Path) -> Path:
    return (root / Path(value)).resolve()


def load_yaml(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if yaml is not None:
        return yaml.safe_load(text) or {}
    return parse_simple_yaml(text)


def parse_simple_yaml(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    current_section = ""
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" ") and line.endswith(":"):
            current_section = line[:-1].strip()
            data[current_section] = {}
            continue
        if current_section and line.startswith("  ") and ":" in line:
            key, value = line.strip().split(":", 1)
            data[current_section][key.strip()] = parse_scalar(value.strip())
    return data


def parse_scalar(value: str) -> Any:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    lower = text.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    try:
        return int(text)
    except Exception:
        pass
    try:
        return float(text)
    except Exception:
        return text
