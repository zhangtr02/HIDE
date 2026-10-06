from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass(frozen=True)
class RustFile:
    id: str
    abs_path: Path
    module_id: str
    basename: str


@dataclass
class RustModule:
    id: str
    abs_path: Path
    crate_id: str
    is_root: bool = False
    files: List[RustFile] = field(default_factory=list)


@dataclass
class RustCrate:
    id: str
    abs_path: Path
    modules: List[str] = field(default_factory=list)
    files: List[RustFile] = field(default_factory=list)


@dataclass
class RustTree:
    crates: Dict[str, RustCrate] = field(default_factory=dict)
    modules: Dict[str, RustModule] = field(default_factory=dict)

    def all_files(self) -> List[RustFile]:
        out: List[RustFile] = []
        for module in self.modules.values():
            out.extend(module.files)
        return out
