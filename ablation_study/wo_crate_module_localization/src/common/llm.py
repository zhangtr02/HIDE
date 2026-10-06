from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from json import JSONDecodeError
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
            "messages": messages,
            "temperature": self.temperature,
            "max_completion_tokens": max_tokens or self.max_tokens,
        }
        if self.prefer_json_object:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(self.request_retries):
            response: Any = None
            text = ""
            try:
                response = self._create(stage=stage, payload=payload)
                text = extract_chat_completion_text(response).strip()
                try:
                    return parse_json_response_text(text)
                except JSONDecodeError as parse_error:
                    return self._repair_json_response(text=text, stage=stage, max_tokens=max_tokens or self.max_tokens, parse_error=parse_error)
            except Exception as exc:
                last_error = exc
                print_llm_debug(stage=stage, attempt=attempt + 1, text=text, response=response)
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
        response = self.client.chat.completions.create(**payload)
        latency = time.time() - start
        usage = response.usage.model_dump() if getattr(response, "usage", None) else {}
        self.usage_tracker.add(stage=stage, latency_sec=latency, usage=usage)
        return response

    def _repair_json_response(self, *, text: str, stage: str, max_tokens: int, parse_error: JSONDecodeError) -> Any:
        repair_payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You repair malformed JSON. Return only valid JSON. "
                        "Do not add, remove, rename, or reorder semantic items unless required to make JSON valid."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"The following JSON failed to parse: {parse_error}. "
                        "Fix only JSON syntax and return the corrected JSON.\n\n"
                        f"{strip_markdown_json_fence(text)}"
                    ),
                },
            ],
            "temperature": 0.0,
            "max_completion_tokens": min(max_tokens, 4000),
        }
        if self.prefer_json_object:
            repair_payload["response_format"] = {"type": "json_object"}
        repair_response = self._create(stage=f"{stage}_json_repair", payload=repair_payload)
        repair_text = extract_chat_completion_text(repair_response).strip()
        return parse_json_response_text(repair_text)


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
    last_raw: Any = None
    for attempt in range(retries + 1):
        current_messages = list(messages)
        if attempt and last_error is not None:
            current_messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Your previous JSON failed {schema_name} validation: {last_error}. "
                        f"The invalid response was: {preview_value(last_raw, max_chars=1000)}. "
                        "Do not return commands, tool calls, searches, or an investigation plan. "
                        "Use only the candidates and evidence already supplied in the original request. "
                        "Return JSON only and satisfy the original schema exactly."
                    ),
                }
            )
        raw = client.chat_json(current_messages, max_tokens=max_tokens, stage=stage if not attempt else f"{stage}_repair")
        last_raw = raw
        try:
            return validator(raw)
        except Exception as exc:
            last_error = exc
    raise RuntimeError(
        f"LLM output failed {schema_name} validation: {last_error}; raw={preview_value(last_raw)}"
    ) from last_error


def extract_chat_completion_text(response: Any) -> str:
    chunks: List[str] = []
    choices = getattr(response, "choices", None)
    if isinstance(choices, list):
        for choice in choices:
            message = getattr(choice, "message", None)
            content = getattr(message, "content", None)
            chunks.extend(extract_content_parts(content))
    return "\n".join(chunks)


def preview_value(value: Any, *, max_chars: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = repr(value)
    text = text.replace("\n", "\\n")
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def strip_markdown_json_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 3 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return stripped


def parse_json_response_text(text: str) -> Any:
    stripped = strip_markdown_json_fence(text)
    candidates = unique_texts(
        [
            stripped,
            extract_json_object_text(stripped),
            repair_common_json_syntax(stripped),
            repair_common_json_syntax(extract_json_object_text(stripped)),
        ]
    )
    last_error: JSONDecodeError | None = None
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except JSONDecodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    raise JSONDecodeError("empty response", stripped, 0)


def extract_json_object_text(text: str) -> str:
    stripped = text.strip()
    object_start = stripped.find("{")
    object_end = stripped.rfind("}")
    if object_start >= 0 and object_end > object_start:
        return stripped[object_start : object_end + 1].strip()
    array_start = stripped.find("[")
    array_end = stripped.rfind("]")
    if array_start >= 0 and array_end > array_start:
        return stripped[array_start : array_end + 1].strip()
    return stripped


def repair_common_json_syntax(text: str) -> str:
    repaired = text.strip()
    if not repaired:
        return repaired
    # Common model mistakes: missing commas between adjacent objects/properties
    # and trailing commas before closing braces/brackets.
    repaired = re.sub(r"}\s*\n\s*(?=\{)", "},\n", repaired)
    repaired = re.sub(r"(?<=[}\"\\]\d])\s*\n\s*(?=\"[^\"]+\"\s*:)", ",\n", repaired)
    repaired = re.sub(r",\s*([}\]])", r"\1", repaired)
    return repaired


def unique_texts(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def print_llm_debug(*, stage: str, attempt: int, text: str, response: Any, max_chars: int = 1200) -> None:
    if response is None:
        return
    print(
        f"[llm-debug] stage={stage} attempt={attempt} text_len={len(text)} text={preview_text(text, max_chars=max_chars)}",
        flush=True,
    )
    if not text:
        print(
            f"[llm-debug] stage={stage} attempt={attempt} raw_response={preview_response(response, max_chars=max_chars)}",
            flush=True,
        )


def preview_text(text: str, *, max_chars: int = 1200) -> str:
    if not text:
        return "<empty>"
    text = text.replace("\n", "\\n")
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def preview_response(response: Any, *, max_chars: int = 1200) -> str:
    try:
        if hasattr(response, "model_dump"):
            obj = response.model_dump(exclude_none=True)
        elif hasattr(response, "to_dict"):
            obj = response.to_dict()
        else:
            obj = repr(response)
        text = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        text = repr(response)
    text = text.replace("\n", "\\n")
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


def extract_content_parts(content: Any) -> List[str]:
    if content is None:
        return []
    if isinstance(content, str):
        return [content]
    chunks: List[str] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if text:
                    chunks.append(str(text))
            else:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(str(text))
    return chunks


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
