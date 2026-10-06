from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List

from .config import RuntimeConfig
from .context import BugContext, build_bug_context
from .git_utils import checkout_ref, get_head
from .io_utils import chunked, unique_keep_order, write_json
from .llm import LLMClient
from .methods import MethodCandidate, MethodStore
from .prompts import (
    file_summary_messages,
    method_doc_messages,
    method_review_messages,
    related_methods_messages,
    suspicious_files_messages,
    test_behavior_messages,
    test_failure_messages,
)
from .repo import fallback_file_summary, list_compiler_rs_files, read_file_context


@dataclass(frozen=True)
class PipelineOptions:
    checkout: bool = True
    build_if_log_missing: bool = False


@dataclass(frozen=True)
class FileCandidate:
    path: str
    summary: str


class SoapFLRustcPipeline:
    def __init__(self, *, cfg: RuntimeConfig, client: LLMClient, options: PipelineOptions) -> None:
        self.cfg = cfg
        self.client = client
        self.options = options

    def run_bug(self, *, bug_id: str, toolchain: str, build_args: str) -> Dict[str, Any]:
        total_start = time.time()
        stage_times: Dict[str, float] = {}
        usage_start_index = len(self.client.usage_tracker.calls)

        checkout_commit = ""
        if self.options.checkout and toolchain:
            checkout_commit = timed_value(stage_times, "checkout", lambda: checkout_ref(self.cfg.paths.rust_repo_dir, toolchain))
        else:
            checkout_commit = timed_value(stage_times, "get_head", lambda: get_head(self.cfg.paths.rust_repo_dir))

        ctx = timed_value(
            stage_times,
            "load_bug_context",
            lambda: build_bug_context(
                self.cfg,
                bug_id=bug_id,
                toolchain=toolchain,
                build_args=build_args,
                build_if_log_missing=self.options.build_if_log_missing,
            ),
        )

        behavior = timed_value(stage_times, "test_behavior_analysis", lambda: self.run_test_behavior_analysis(ctx))
        failure = timed_value(stage_times, "test_failure_analysis", lambda: self.run_test_failure_analysis(ctx, behavior))
        file_candidates = timed_value(stage_times, "load_file_candidates", self.load_file_candidates)
        selected_files = timed_value(
            stage_times,
            "search_suspicious_files",
            lambda: self.run_search_suspicious_files(ctx, behavior, failure, file_candidates),
        )
        method_artifacts: Dict[str, Any] = {"enabled": False, "reviewed_methods": []}
        if self.cfg.soapfl.run_method_level:
            method_artifacts = timed_value(
                stage_times,
                "method_level_localization",
                lambda: self.run_method_level_localization(ctx, behavior, failure, selected_files),
            )
            method_artifacts["enabled"] = True

        final_methods = method_artifacts["reviewed_methods"][: self.cfg.soapfl.method_top_k]
        report = {
            "bug_id": bug_id,
            "method_level_enabled": self.cfg.soapfl.run_method_level,
            "checkout": {
                "requested": toolchain,
                "resolved": checkout_commit,
            },
            "final_top10": [item["path"] for item in selected_files[: self.cfg.soapfl.file_top_n]],
            "final_method_top10": [item["id"] for item in final_methods],
            "final_method_items": final_methods,
            "fault_comprehension": {
                "test_behavior_analysis": behavior,
                "test_failure_analysis": failure,
            },
            "file_level": {
                "candidate_file_count": len(file_candidates),
                "selected_files": selected_files,
            },
            "method_level": method_artifacts,
            "efficiency": {
                "total_wall_time_sec": round(time.time() - total_start, 3),
                "stage_times_sec": {key: round(value, 3) for key, value in stage_times.items()},
                "llm_usage": self.client.usage_tracker.to_dict(start_index=usage_start_index),
            },
        }
        write_json(self.cfg.paths.report_dir / f"{bug_id}.json", report)
        return report

    def run_test_behavior_analysis(self, ctx: BugContext) -> Dict[str, Any]:
        payload = {
            "bug_id": ctx.bug_id,
            "reproducer_code": ctx.reproducer_code,
        }
        return self.client.chat_json(
            stage="test_behavior_analysis",
            messages=test_behavior_messages(payload),
            max_tokens=self.cfg.llm.max_tokens,
        )

    def run_test_failure_analysis(self, ctx: BugContext, behavior: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "bug_id": ctx.bug_id,
            "reproducer_code": ctx.reproducer_code,
            "error_message": ctx.error_message,
            "test_behavior": behavior,
        }
        return self.client.chat_json(
            stage="test_failure_analysis",
            messages=test_failure_messages(payload),
            max_tokens=1200,
        )

    def load_file_candidates(self) -> List[FileCandidate]:
        candidates: List[FileCandidate] = []
        for file_id in list_compiler_rs_files(self.cfg.paths.rust_repo_dir):
            candidates.append(FileCandidate(path=file_id, summary=self.load_or_create_file_summary(file_id)))
        return candidates

    def load_or_create_file_summary(self, file_id: str) -> str:
        doc_path = self.file_doc_path(file_id)
        if doc_path.exists():
            try:
                data = read_json_dict(doc_path)
                summary = str(data.get("summary") or "").strip()
                if summary:
                    return summary
            except Exception:
                pass

        context = read_file_context(self.cfg.paths.rust_repo_dir, file_id, max_chars=self.cfg.soapfl.file_summary_chars)
        summary = fallback_file_summary(self.cfg.paths.rust_repo_dir, file_id, max_chars=self.cfg.soapfl.file_summary_chars)
        if context:
            try:
                response = self.client.chat_json(
                    stage=f"file_summary:{short_stage_name(file_id)}",
                    messages=file_summary_messages({"path": file_id, "file_context": context}),
                    max_tokens=self.cfg.soapfl.file_summary_max_tokens,
                )
                llm_summary = str(response.get("summary") or "").strip()
                if llm_summary:
                    summary = llm_summary
            except Exception:
                pass

        write_json(doc_path, {"doc_type": "file", "path": file_id, "summary": summary})
        return summary

    def file_doc_path(self, file_id: str) -> Path:
        return self.cfg.paths.docs_file_dir / file_doc_relative_path(file_id)

    def run_search_suspicious_files(
        self,
        ctx: BugContext,
        behavior: Dict[str, Any],
        failure: Dict[str, Any],
        candidates: List[FileCandidate],
    ) -> List[Dict[str, Any]]:
        chunk_selected: List[Dict[str, str]] = []
        for index, chunk in enumerate(chunked(candidates, self.cfg.soapfl.file_chunk_size), start=1):
            payload = self.file_search_payload(
                ctx=ctx,
                behavior=behavior,
                failure=failure,
                candidates=chunk,
                requested_count=self.cfg.soapfl.file_chunk_select_n,
            )
            response = self.client.chat_json(
                stage=f"search_suspicious_files_chunk_{index}",
                messages=suspicious_files_messages(payload),
                max_tokens=1000,
            )
            chunk_selected.extend(parse_file_selection(response, {item.path for item in chunk}))

        if chunk_selected:
            shortlist_paths = unique_keep_order(item["path"] for item in chunk_selected)
            shortlist_index = {item.path: item for item in candidates if item.path in set(shortlist_paths)}
            final_candidates = [shortlist_index[path] for path in shortlist_paths if path in shortlist_index]
        else:
            final_candidates = candidates

        payload = self.file_search_payload(
            ctx=ctx,
            behavior=behavior,
            failure=failure,
            candidates=final_candidates,
            requested_count=self.cfg.soapfl.file_top_n,
        )
        response = self.client.chat_json(
            stage="search_suspicious_files_final",
            messages=suspicious_files_messages(payload),
            max_tokens=1400,
        )
        selected = parse_file_selection(response, {item.path for item in final_candidates})
        selected = unique_file_items(selected)

        if len(selected) < self.cfg.soapfl.file_top_n:
            selected_paths = {item["path"] for item in selected}
            for candidate in final_candidates:
                if candidate.path in selected_paths:
                    continue
                selected.append({"path": candidate.path, "reason": "Fallback selection due to invalid or incomplete model output."})
                selected_paths.add(candidate.path)
                if len(selected) >= min(self.cfg.soapfl.file_top_n, len(final_candidates)):
                    break
        return selected[: self.cfg.soapfl.file_top_n]

    def file_search_payload(
        self,
        *,
        ctx: BugContext,
        behavior: Dict[str, Any],
        failure: Dict[str, Any],
        candidates: List[FileCandidate],
        requested_count: int,
    ) -> Dict[str, Any]:
        return {
            "bug_id": ctx.bug_id,
            "failed_reproducer": "rustc compiler bug reproducer",
            "reproducer_code": ctx.reproducer_code,
            "error_message": ctx.error_message,
            "test_behavior": behavior,
            "test_failure_causes": failure,
            "requested_count": requested_count,
            "candidate_source_files_list": file_table(candidates),
        }

    def run_method_level_localization(
        self,
        ctx: BugContext,
        behavior: Dict[str, Any],
        failure: Dict[str, Any],
        selected_files: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        method_store = MethodStore(rust_repo_dir=self.cfg.paths.rust_repo_dir)
        file_docs: Dict[str, str] = {}
        methods_by_file: Dict[str, List[MethodCandidate]] = {}
        method_docs: Dict[str, str] = {}
        related_methods: List[Dict[str, Any]] = []

        for file_item in selected_files:
            file_id = str(file_item.get("path") or "")
            if not file_id:
                continue
            file_docs[file_id] = self.load_or_create_file_summary(file_id)
            methods = method_store.load_methods_for_file(file_id)
            methods_by_file[file_id] = methods
            method_docs.update(self.run_method_doc_enhancement(file_id=file_id, file_doc=file_docs[file_id], methods=methods))
            related_methods.extend(
                self.run_find_related_methods(
                    ctx=ctx,
                    behavior=behavior,
                    failure=failure,
                    file_id=file_id,
                    file_doc=file_docs[file_id],
                    methods=methods,
                    method_docs=method_docs,
                )
            )

        related_methods = unique_method_items(related_methods)
        if len(related_methods) > self.cfg.soapfl.max_methods_to_review:
            related_methods = related_methods[: self.cfg.soapfl.max_methods_to_review]

        method_index = {
            method.id: method
            for methods in methods_by_file.values()
            for method in methods
        }
        reviewed_methods = self.run_method_review(
            ctx=ctx,
            behavior=behavior,
            failure=failure,
            method_index=method_index,
            method_docs=method_docs,
            related_methods=related_methods,
            file_docs=file_docs,
        )

        return {
            "input_files": [item["path"] for item in selected_files],
            "methods_by_file": {
                file_id: [method.to_report_dict(summary=method_docs.get(method.id, ""), source="candidate") for method in methods]
                for file_id, methods in methods_by_file.items()
            },
            "method_docs": method_docs,
            "related_methods": related_methods,
            "reviewed_methods": reviewed_methods,
        }

    def run_method_doc_enhancement(self, *, file_id: str, file_doc: str, methods: List[MethodCandidate]) -> Dict[str, str]:
        if not methods:
            return {}
        docs: Dict[str, str] = {}
        missing: List[MethodCandidate] = []
        for method in methods:
            doc = self.load_method_doc(method)
            if doc:
                docs[method.id] = doc
            else:
                missing.append(method)
        for batch_index, batch in enumerate(chunked(missing, self.cfg.soapfl.method_doc_chunk_size), start=1):
            payload = {
                "file_path": file_id,
                "file_documentation": file_doc,
                "methods": [
                    method.to_prompt_dict(include_body=True, body_chars=self.cfg.soapfl.method_doc_code_chars)
                    for method in batch
                ],
            }
            try:
                response = self.client.chat_json(
                    stage=f"method_doc_enhancement:{short_stage_name(file_id)}:{batch_index}",
                    messages=method_doc_messages(payload),
                    max_tokens=self.cfg.soapfl.method_doc_max_tokens,
                )
                summaries = parse_method_summaries(response, {method.id for method in batch})
            except Exception:
                summaries = {}
            for method in batch:
                summary = summaries.get(method.id) or fallback_method_summary(method)
                docs[method.id] = summary
                self.write_method_doc(method, summary)
        return docs

    def load_method_doc(self, method: MethodCandidate) -> str:
        path = self.method_doc_path(method)
        if path.exists():
            try:
                data = read_json_dict(path)
                summary = str(data.get("summary") or "").strip()
                if summary:
                    return summary
            except Exception:
                pass
        return ""

    def write_method_doc(self, method: MethodCandidate, summary: str) -> None:
        write_json(
            self.method_doc_path(method),
            {
                "id": method.id,
                "doc_key": method_doc_key(method),
                "doc_type": "method",
                "file": method.file,
                "item_type": method.item_type,
                "qualified_name": method.qualified_name,
                "summary": summary,
            },
        )

    def method_doc_path(self, method: MethodCandidate) -> Path:
        return doc_dir_for_file(self.cfg.paths.docs_method_dir, method.file) / method_doc_filename(method)

    def run_find_related_methods(
        self,
        *,
        ctx: BugContext,
        behavior: Dict[str, Any],
        failure: Dict[str, Any],
        file_id: str,
        file_doc: str,
        methods: List[MethodCandidate],
        method_docs: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        if not methods:
            return []
        payload = {
            "bug_id": ctx.bug_id,
            "failed_reproducer": "rustc compiler bug reproducer",
            "reproducer_code": ctx.reproducer_code,
            "error_message": ctx.error_message,
            "test_behavior": behavior,
            "test_failure_causes": failure,
            "file_path": file_id,
            "file_documentation": file_doc,
            "methods_list": methods_list_table(methods, method_docs),
            "requested_count": self.cfg.soapfl.related_methods_per_file,
        }
        response = self.client.chat_json(
            stage=f"find_related_methods:{short_stage_name(file_id)}",
            messages=related_methods_messages(payload),
            max_tokens=self.cfg.soapfl.method_selection_max_tokens,
        )
        selected = parse_method_selection(response, {method.id for method in methods})
        selected = unique_method_items(selected)
        if not selected:
            selected = [
                {"id": method.id, "reason": "Fallback selection due to invalid or empty model output."}
                for method in methods[: self.cfg.soapfl.related_methods_per_file]
            ]
        return selected[: self.cfg.soapfl.related_methods_per_file]

    def run_method_review(
        self,
        *,
        ctx: BugContext,
        behavior: Dict[str, Any],
        failure: Dict[str, Any],
        method_index: Dict[str, MethodCandidate],
        method_docs: Dict[str, str],
        related_methods: List[Dict[str, Any]],
        file_docs: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        reviewed: List[Dict[str, Any]] = []
        for item in related_methods:
            method_id = str(item.get("id") or "")
            method = method_index.get(method_id)
            if method is None:
                continue
            method_doc = method_docs.get(method.id, fallback_method_summary(method))
            payload = {
                "bug_id": ctx.bug_id,
                "failed_reproducer": "rustc compiler bug reproducer",
                "reproducer_code": ctx.reproducer_code,
                "error_message": ctx.error_message,
                "test_behavior": behavior,
                "test_failure_causes": failure,
                "source_file": method.file,
                "file_documentation": file_docs.get(method.file, ""),
                "suspicious_method": method.to_prompt_dict(
                    include_body=True,
                    body_chars=self.cfg.soapfl.method_review_code_chars,
                    summary=method_doc,
                ),
                "related_selection_reason": item.get("reason", ""),
            }
            response = self.client.chat_json(
                stage=f"method_review:{short_stage_name(method.item_name)}",
                messages=method_review_messages(payload),
                max_tokens=self.cfg.soapfl.method_review_max_tokens,
            )
            score = clamp_score(response.get("score") if isinstance(response, dict) else 0)
            reviewed.append(
                method.to_report_dict(
                    score=score,
                    reason=str(response.get("reason") or "").strip() if isinstance(response, dict) else "",
                    summary=method_doc,
                    source="method_review",
                )
            )
        reviewed.sort(key=lambda item: (-int(item.get("score") or 0), str(item.get("id") or "")))
        return reviewed


def timed_value(stage_times: Dict[str, float], key: str, fn: Callable[[], Any]) -> Any:
    start = time.time()
    value = fn()
    stage_times[key] = time.time() - start
    return value


def file_table(candidates: List[FileCandidate]) -> str:
    rows = [
        [str(index), item.path, item.summary]
        for index, item in enumerate(candidates, start=1)
    ]
    return markdown_table(["Index", "Path", "File Documentation"], rows)


def methods_list_table(methods: List[MethodCandidate], method_docs: Dict[str, str]) -> str:
    rows = []
    for index, method in enumerate(methods, start=1):
        rows.append(
            [
                str(index),
                method.id,
                method.item_type,
                method.qualified_name,
                f"{method.start_line}-{method.end_line}",
                method_docs.get(method.id, fallback_method_summary(method)),
                ", ".join(method.call_list[:12]),
            ]
        )
    return markdown_table(["Index", "Method ID", "Kind", "Method Full Name", "Lines", "Method Comment", "Calls"], rows)


def markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        out.append("| " + " | ".join(clean_table_cell(item) for item in row) + " |")
    return "\n".join(out)


def clean_table_cell(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "/").strip()
    return text[:1200]


def parse_file_selection(response: Dict[str, Any], allowed: set[str]) -> List[Dict[str, str]]:
    files = response.get("files") if isinstance(response, dict) else []
    out: List[Dict[str, str]] = []
    if not isinstance(files, list):
        return out
    for item in files:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if path in allowed:
            out.append({"path": path, "reason": str(item.get("reason") or "").strip()})
    return out


def parse_method_summaries(response: Dict[str, Any], allowed: set[str]) -> Dict[str, str]:
    methods = response.get("methods") if isinstance(response, dict) else []
    out: Dict[str, str] = {}
    if not isinstance(methods, list):
        return out
    for item in methods:
        if not isinstance(item, dict):
            continue
        method_id = str(item.get("id") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if method_id in allowed and summary:
            out[method_id] = summary
    return out


def parse_method_selection(response: Dict[str, Any], allowed: set[str]) -> List[Dict[str, str]]:
    methods = response.get("methods") if isinstance(response, dict) else []
    out: List[Dict[str, str]] = []
    if not isinstance(methods, list):
        return out
    for item in methods:
        if not isinstance(item, dict):
            continue
        method_id = str(item.get("id") or "").strip()
        if method_id in allowed:
            out.append({"id": method_id, "reason": str(item.get("reason") or "").strip()})
    return out


def unique_file_items(items: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        out.append({"path": path, "reason": str(item.get("reason") or "").strip()})
    return out


def unique_method_items(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        method_id = str(item.get("id") or "").strip()
        if not method_id or method_id in seen:
            continue
        seen.add(method_id)
        out.append({"id": method_id, "reason": str(item.get("reason") or "").strip()})
    return out


def fallback_method_summary(method: MethodCandidate) -> str:
    parts = [
        f"{method.item_type} {method.qualified_name} in {method.file}, lines {method.start_line}-{method.end_line}.",
        f"Signature: {method.signature}",
    ]
    if method.call_list:
        parts.append("Calls: " + ", ".join(method.call_list[:12]) + ".")
    return " ".join(parts)


def read_json_dict(path: Path) -> Dict[str, Any]:
    import json

    obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return obj if isinstance(obj, dict) else {}


def file_doc_relative_path(file_id: str) -> Path:
    path = Path(file_id)
    if path.suffix:
        return path.with_suffix(".json")
    return path / "index.json"


def doc_dir_for_file(root_dir: Path, file_id: str) -> Path:
    path = Path(file_id)
    if path.suffix:
        path = path.with_suffix("")
    return root_dir / path


def method_doc_key(method: MethodCandidate) -> str:
    return f"{method.file}::{method.item_type}::{method.qualified_name}"


def method_doc_filename(method: MethodCandidate) -> str:
    key = method_doc_key(method)
    stem = safe_filename(f"{method.item_type}__{method.qualified_name}", max_chars=120)
    return f"{stem}__{sha1_text(key)[:12]}.json"


def safe_filename(value: str, *, max_chars: int) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value or "method"))
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip("_")
    return cleaned or "method"


def short_stage_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(value or "item"))
    return cleaned[-80:] or "item"


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def clamp_score(value: Any) -> int:
    try:
        score = int(value)
    except Exception:
        score = 0
    return max(0, min(10, score))
