from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import LLMConfig


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


class GraphLocatorLLM:
    def __init__(self, cfg: LLMConfig) -> None:
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("Missing Python package 'openai'. Install it before running graphlocator_rustc.") from exc

        api_key_path = Path.home() / ".openai_api_key"
        dotfile_key = api_key_path.read_text(encoding="utf-8").strip() if api_key_path.exists() else ""
        api_key = os.environ.get("OPENAI_API_KEY") or dotfile_key
        if not api_key:
            raise RuntimeError("Missing API key: set OPENAI_API_KEY.")
        self.cfg = cfg
        self.client = OpenAI(base_url=cfg.base_url, api_key=api_key, timeout=cfg.timeout_sec)
        self.usage_tracker = LLMUsageTracker(model=cfg.model)

    def completion(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        max_completion_tokens: int | None = None,
        stage: str = "completion",
    ) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
        payload: Dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "temperature": self.cfg.temperature,
            "max_completion_tokens": max_completion_tokens or self.cfg.max_completion_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["parallel_tool_calls"] = False

        last_error: Optional[Exception] = None
        for attempt in range(5):
            try:
                start = time.time()
                response = self.client.chat.completions.create(**payload)
                latency = time.time() - start
                decoded = [self._message_to_dict(choice.message) for choice in response.choices]
                finish = [str(choice.finish_reason or "") for choice in response.choices]
                usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
                self.usage_tracker.add(stage=stage, latency_sec=latency, usage=usage)
                return decoded, finish, usage
            except Exception as exc:
                last_error = exc
                fallback = self._maybe_fallback_max_tokens(payload, exc)
                if fallback:
                    payload = fallback
                time.sleep(2.0 + attempt)
        raise RuntimeError(f"LLM request failed after retries: {last_error}") from last_error

    @staticmethod
    def _maybe_fallback_max_tokens(payload: Dict[str, Any], exc: Exception) -> Dict[str, Any] | None:
        msg = str(exc)
        if "max_completion_tokens" not in payload or "max_completion_tokens" not in msg:
            return None
        new_payload = dict(payload)
        new_payload["max_tokens"] = new_payload.pop("max_completion_tokens")
        return new_payload

    @staticmethod
    def _message_to_dict(message: Any) -> Dict[str, Any]:
        if hasattr(message, "model_dump"):
            obj = message.model_dump(exclude_none=True)
        elif hasattr(message, "to_dict"):
            obj = message.to_dict()
        else:
            obj = dict(message)

        tool_calls = obj.get("tool_calls") or []
        normalized_tool_calls: List[Dict[str, Any]] = []
        for call in tool_calls:
            if hasattr(call, "model_dump"):
                call = call.model_dump(exclude_none=True)
            function_obj = call.get("function") or {}
            normalized_tool_calls.append({
                "id": call.get("id"),
                "type": call.get("type") or "function",
                "function": {
                    "name": function_obj.get("name"),
                    "arguments": function_obj.get("arguments") or "{}",
                },
            })
        out: Dict[str, Any] = {
            "role": obj.get("role") or "assistant",
            "content": obj.get("content"),
        }
        if normalized_tool_calls:
            out["tool_calls"] = normalized_tool_calls
        return out


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
