from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class Candidate:
    id: str
    reason: str

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "reason": self.reason}


@dataclass
class CandidateResult:
    candidates: List[Candidate] = field(default_factory=list)

    def ids(self) -> List[str]:
        return [candidate.id for candidate in self.candidates]

    def to_dict(self) -> Dict[str, Any]:
        return {"candidates": [candidate.to_dict() for candidate in self.candidates]}
