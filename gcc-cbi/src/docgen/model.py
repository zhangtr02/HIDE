from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class GCCFile:
    id: str
    abs_path: Path
    module_id: str
    basename: str


@dataclass
class GCCModule:
    id: str
    abs_path: Path
    files: List[GCCFile] = field(default_factory=list)


@dataclass
class GCCTree:
    modules: Dict[str, GCCModule] = field(default_factory=dict)

    def all_files(self) -> List[GCCFile]:
        out: List[GCCFile] = []
        for module in self.modules.values():
            out.extend(module.files)
        return out
