from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path(__file__)).resolve()
    if current.is_file():
        current = current.parent
    for candidate in [current, *current.parents]:
        if (candidate / "config" / "config.yaml").exists():
            return candidate
    raise FileNotFoundError(f"Cannot locate project root from {current}")


def load_config() -> Dict[str, Any]:
    root = find_project_root(Path(__file__).resolve())
    config_path = root / "config" / "config.yaml"
    cfg = load_yaml(config_path)

    paths = cfg.get("paths") or {}
    resolved_paths: Dict[str, Path] = {}
    for key, value in paths.items():
        path = Path(str(value))
        resolved_paths[key] = path if path.is_absolute() else root / path

    cfg["paths"] = resolved_paths
    cfg["_project_root"] = root
    cfg["_config_path"] = config_path
    return cfg


def load_yaml(path: Path) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore

        with path.open("r", encoding="utf-8") as handle:
            obj = yaml.safe_load(handle) or {}
        return obj if isinstance(obj, dict) else {}
    except ModuleNotFoundError:
        return parse_simple_yaml(path.read_text(encoding="utf-8"))


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
    if not value:
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
