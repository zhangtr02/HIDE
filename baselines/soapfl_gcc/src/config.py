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
    docs_file_dir: Path
    docs_method_dir: Path


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    temperature: float
    timeout_sec: int
    max_tokens: int
    prefer_json_object: bool


@dataclass(frozen=True)
class SoapFLConfig:
    file_top_n: int
    method_top_k: int
    file_chunk_size: int
    file_chunk_select_n: int
    issue_chars: int
    file_summary_chars: int
    method_search_chunk_size: int
    method_search_chunk_select_n: int
    related_methods_per_file: int
    max_methods_to_review: int
    method_review_batch_size: int
    method_review_code_chars: int
    method_selection_max_tokens: int
    method_review_max_tokens: int


@dataclass(frozen=True)
class RuntimeConfig:
    paths: PathsConfig
    llm: LLMConfig
    soapfl: SoapFLConfig


def load_config() -> RuntimeConfig:
    root = Path(__file__).resolve().parents[1]
    raw = load_yaml(root / "config" / "config.yaml")
    paths = raw.get("paths") or {}
    llm = raw.get("llm") or {}
    soapfl = raw.get("soapfl") or {}
    return RuntimeConfig(
        paths=PathsConfig(
            root=root,
            gcc_repo_dir=resolve(root, paths["gcc_repo_dir"]),
            csv_file=resolve(root, paths["csv_file"]),
            issue_dir=resolve(root, paths["issue_dir"]),
            groundtruth_dir=resolve(root, paths["groundtruth_dir"]),
            method_groundtruth_dir=resolve(root, paths["method_groundtruth_dir"]),
            report_dir=resolve(root, paths.get("report_dir", "reports")),
            docs_file_dir=resolve(root, paths.get("docs_file_dir", "docs_file")),
            docs_method_dir=resolve(root, paths.get("docs_method_dir", "docs_method")),
        ),
        llm=LLMConfig(
            base_url=str(llm.get("base_url") or "https://api.openai.com/v1"),
            model=str(llm.get("model") or "gpt-5.4"),
            temperature=float(llm.get("temperature", 0.0)),
            timeout_sec=int(llm.get("timeout_sec", 120)),
            max_tokens=int(llm.get("max_tokens", 1600)),
            prefer_json_object=bool(llm.get("prefer_json_object", True)),
        ),
        soapfl=SoapFLConfig(
            file_top_n=int(soapfl.get("file_top_n", 10)),
            method_top_k=int(soapfl.get("method_top_k", 10)),
            file_chunk_size=int(soapfl.get("file_chunk_size", 500)),
            file_chunk_select_n=int(soapfl.get("file_chunk_select_n", 5)),
            issue_chars=int(soapfl.get("issue_chars", 30000)),
            file_summary_chars=int(soapfl.get("file_summary_chars", 800)),
            method_search_chunk_size=int(soapfl.get("method_search_chunk_size", 160)),
            method_search_chunk_select_n=int(soapfl.get("method_search_chunk_select_n", 3)),
            related_methods_per_file=int(soapfl.get("related_methods_per_file", 2)),
            max_methods_to_review=int(soapfl.get("max_methods_to_review", 15)),
            method_review_batch_size=int(soapfl.get("method_review_batch_size", 10)),
            method_review_code_chars=int(soapfl.get("method_review_code_chars", 2500)),
            method_selection_max_tokens=int(soapfl.get("method_selection_max_tokens", 1400)),
            method_review_max_tokens=int(soapfl.get("method_review_max_tokens", 900)),
        ),
    )


def resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (root / path).resolve()


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
