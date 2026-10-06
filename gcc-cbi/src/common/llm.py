from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, TypeVar


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
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.0,
        timeout_sec: int = 120,
        max_tokens: int = 2000,
        prefer_json_object: bool = True,
        request_retries: int = 8,
        retry_initial_delay_sec: float = 2.0,
        retry_max_delay_sec: float = 60.0,
    ) -> None:
        if not api_key:
            raise RuntimeError("Missing API key: set OPENAI_API_KEY.")
        try:
            from openai import OpenAI
        except Exception as exc:
            raise RuntimeError("Missing Python package 'openai'. Install it before running the pipeline.") from exc

        self.client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout_sec)
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.prefer_json_object = prefer_json_object
        self.request_retries = max(1, request_retries)
        self.retry_initial_delay_sec = max(0.1, retry_initial_delay_sec)
        self.retry_max_delay_sec = max(self.retry_initial_delay_sec, retry_max_delay_sec)
        self.usage_tracker = LLMUsageTracker(model=model)

    @classmethod
    def from_config(cls, cfg: Dict[str, Any]) -> "LLMClient":
        llm_cfg = cfg.get("llm") or {}
        api_key = os.environ.get("OPENAI_API_KEY") or read_api_key_dotfile()
        return cls(
            base_url=str(llm_cfg.get("base_url") or "https://api.openai.com/v1"),
            api_key=api_key,
            model=str(llm_cfg.get("model") or "gpt-5.4"),
            temperature=float(llm_cfg.get("temperature", 0.0)),
            timeout_sec=int(llm_cfg.get("timeout_sec", 120)),
            max_tokens=int(llm_cfg.get("max_tokens", 2000)),
            prefer_json_object=bool(llm_cfg.get("prefer_json_object", True)),
            request_retries=int(llm_cfg.get("request_retries", 8)),
            retry_initial_delay_sec=float(llm_cfg.get("retry_initial_delay_sec", 2.0)),
            retry_max_delay_sec=float(llm_cfg.get("retry_max_delay_sec", 60.0)),
        )

    def chat_json(self, messages: List[Dict[str, Any]], *, max_tokens: int | None = None, stage: str = "llm") -> Any:
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": messages,
            "temperature": self.temperature,
            "max_output_tokens": max_tokens or self.max_tokens,
        }
        if self.prefer_json_object:
            payload["text"] = {"format": {"type": "json_object"}}

        last_error: Exception | None = None
        for attempt in range(self.request_retries):
            try:
                response = self._create(stage=stage, payload=payload)
                text = extract_response_text(response).strip()
                return json.loads(text)
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= self.request_retries:
                    break
                delay = min(self.retry_max_delay_sec, self.retry_initial_delay_sec * (2**attempt))
                print(
                    f"[llm] request failed attempt {attempt + 1}/{self.request_retries}: "
                    f"{short_error(exc)}; retrying in {delay:.1f}s",
                    flush=True,
                )
                time.sleep(delay)
        raise RuntimeError(f"LLM request failed at {stage}: {last_error}") from last_error

    def _create(self, *, stage: str, payload: Dict[str, Any]) -> Any:
        start = time.time()
        response = self.client.responses.create(**payload)
        latency = time.time() - start
        usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
        self.usage_tracker.add(stage=stage, latency_sec=latency, usage=usage)
        return response


T = TypeVar("T")


def chat_json_with_validation(
    client: LLMClient,
    messages: List[Dict[str, Any]],
    validator: Callable[[Any], T],
    *,
    max_tokens: int,
    schema_name: str,
    stage: str = "llm",
    retries: int = 1,
) -> T:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        current_messages = list(messages)
        if attempt and last_error is not None:
            current_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous JSON failed {schema_name} validation: {last_error}. "
                        "Return JSON only and satisfy the schema exactly."
                    ),
                }
            )
        raw = client.chat_json(current_messages, max_tokens=max_tokens, stage=stage if not attempt else f"{stage}_repair")
        try:
            return validator(raw)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"LLM output failed {schema_name} validation: {last_error}") from last_error


def extract_response_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text:
        return str(output_text)
    chunks: List[str] = []
    output = getattr(response, "output", None)
    if isinstance(output, list):
        for item in output:
            content = getattr(item, "content", None)
            if not isinstance(content, list):
                continue
            for part in content:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(str(text))
                elif isinstance(part, dict) and part.get("text"):
                    chunks.append(str(part.get("text")))
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


def short_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:300] + ("..." if len(text) > 300 else "")
