from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .config import LLMConfig


@dataclass(frozen=True)
class LLMCall:
    stage: str
    latency_sec: float
    usage: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {"stage": self.stage, "latency_sec": round(self.latency_sec, 3), **self.usage}


@dataclass
class LLMUsageTracker:
    model: str
    calls: List[LLMCall] = field(default_factory=list)

    def add(self, *, stage: str, latency_sec: float, usage: Dict[str, Any]) -> None:
        self.calls.append(LLMCall(stage=stage, latency_sec=latency_sec, usage=normalize_usage(usage)))

    def to_dict(self, *, start_index: int = 0) -> Dict[str, Any]:
        calls = self.calls[start_index:]
        input_tokens = sum(call.usage.get("input_tokens", 0) for call in calls)
        output_tokens = sum(call.usage.get("output_tokens", 0) for call in calls)
        total_tokens = sum(call.usage.get("total_tokens", 0) for call in calls)
        return {
            "model": self.model,
            "request_count": len(calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens or input_tokens + output_tokens,
            "requests": [call.to_dict() for call in calls],
        }


class LLMClient:
    def __init__(self, cfg: LLMConfig) -> None:
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("Missing Python package 'openai'.") from exc

        api_key = os.environ.get("OPENAI_API_KEY") or read_api_key_dotfile()
        if not api_key:
            raise RuntimeError("Missing API key: set OPENAI_API_KEY.")
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.base_url, api_key=api_key, timeout=cfg.timeout_sec)
        self.usage_tracker = LLMUsageTracker(model=cfg.model)

    def chat_text(self, *, stage: str, prompt: str, max_tokens: int | None = None) -> str:
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.cfg.temperature,
            "max_completion_tokens": max_tokens or self.cfg.max_tokens,
        }
        last_error: Exception | None = None
        for attempt in range(4):
            start = time.time()
            try:
                response = self.client.chat.completions.create(**payload)
                latency = time.time() - start
                usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
                self.usage_tracker.add(stage=stage, latency_sec=latency, usage=usage)
                return extract_chat_completion_text(response).strip()
            except Exception as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM request failed at {stage}: {last_error}") from last_error

def extract_chat_completion_text(response: Any) -> str:
    chunks: List[str] = []
    choices = getattr(response, "choices", None)
    if isinstance(choices, list):
        for choice in choices:
            message = getattr(choice, "message", None)
            content = getattr(message, "content", None)
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    text = getattr(part, "text", None)
                    if text:
                        chunks.append(str(text))
                    elif isinstance(part, dict) and part.get("text"):
                        chunks.append(str(part["text"]))
    return "\n".join(chunks)


def normalize_usage(usage: Dict[str, Any]) -> Dict[str, int]:
    input_tokens = int_value(usage.get("input_tokens", usage.get("prompt_tokens", 0)))
    output_tokens = int_value(usage.get("output_tokens", usage.get("completion_tokens", 0)))
    total_tokens = int_value(usage.get("total_tokens", 0)) or input_tokens + output_tokens
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}


def int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def read_api_key_dotfile() -> str:
    path = Path.home() / ".openai_api_key"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
