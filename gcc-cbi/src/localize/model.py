from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class BugInput:
    bug_id: str
    issue: Dict[str, Any]
    reproducer: Dict[str, Any]
    execution: Dict[str, Any]
    log: Dict[str, Any]
    pass_trace: Dict[str, Any]


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


@dataclass(frozen=True)
class RankedMethod:
    id: str
    rank: int
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "rank": self.rank, "reason": self.reason}


@dataclass
class RankedMethodResult:
    ranked_methods: List[RankedMethod] = field(default_factory=list)

    def top_method_ids(self, k: int = 5) -> List[str]:
        return [item.id for item in sorted(self.ranked_methods, key=lambda item: item.rank)[:k]]

    def to_dict(self) -> Dict[str, Any]:
        return {"ranked_methods": [item.to_dict() for item in self.ranked_methods]}
