from __future__ import annotations

from pathlib import Path


def read_code(path: Path, *, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return text[:head] + "\n\n/* ... truncated ... */\n\n" + text[-tail:]
