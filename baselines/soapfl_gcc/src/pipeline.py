from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List

from .config import RuntimeConfig
from .context import BugContext, build_bug_context
from .git_utils import checkout_ref, get_head
from .io_utils import chunked, load_json_if_exists, unique_keep_order, write_json
from .llm import LLMClient
from .methods import MethodCandidate, MethodStore
from .prompts import (
    batch_method_review_messages,
    related_methods_messages,
    suspicious_files_messages,
    test_behavior_messages,
    test_failure_messages,
)
from .repo import build_file_doc, file_doc_path, list_gcc_source_files


@dataclass(frozen=True)
class PipelineOptions:
    checkout: bool = True


@dataclass(frozen=True)
class FileCandidate:
    path: str
    summary: str


class SoapFLGccPipeline:
    def __init__(self, *, cfg: RuntimeConfig, client: LLMClient, options: PipelineOptions) -> None:
        self.cfg = cfg
        self.client = client
        self.options = options

    def run_bug(self, *, bug_id: str, base_commit: str) -> Dict[str, Any]:
        total_start = time.time()
        stage_times: Dict[str, float] = {}
        usage_start_index = len(self.client.usage_tracker.calls)

        checkout_commit = ""
        if self.options.checkout and base_commit:
            checkout_commit = timed_value(stage_times, "checkout", lambda: checkout_ref(self.cfg.paths.gcc_repo_dir, base_commit))
        else:
            checkout_commit = timed_value(stage_times, "get_head", lambda: get_head(self.cfg.paths.gcc_repo_dir))

        ctx = timed_value(
            stage_times,
            "load_bug_context",
            lambda: build_bug_context(self.cfg.paths.issue_dir, bug_id, max_chars=self.cfg.soapfl.issue_chars),
        )
        behavior = timed_value(stage_times, "test_behavior_analysis", lambda: self.run_test_behavior_analysis(ctx))
        failure = timed_value(stage_times, "test_failure_analysis", lambda: self.run_test_failure_analysis(ctx, behavior))
        file_candidates = timed_value(stage_times, "load_file_candidates", self.load_file_candidates)
        selected_files = timed_value(
            stage_times,
            "search_suspicious_files",
            lambda: self.run_search_suspicious_files(ctx, behavior, failure, file_candidates),
        )
        method_artifacts = timed_value(
            stage_times,
            "method_level_localization",
            lambda: self.run_method_level_localization(ctx, behavior, failure, selected_files),
        )

        final_methods = method_artifacts["reviewed_methods"][: self.cfg.soapfl.method_top_k]
        report = {
            "bug_id": bug_id,
            "checkout": {
                "requested": base_commit,
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
                "summary_source": "static_existing_comments_and_symbols",
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
            "bug_report": ctx.bug_report,
            "reproducer_code": ctx.reproducer_code,
        }
        return self.client.chat_json(
            stage="test_behavior_analysis",
            messages=test_behavior_messages(payload),
            max_tokens=900,
        )

    def run_test_failure_analysis(self, ctx: BugContext, behavior: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "bug_id": ctx.bug_id,
            "bug_report": ctx.bug_report,
            "reproducer_code": ctx.reproducer_code,
            "observed_failure": ctx.error_message,
            "test_behavior": behavior,
        }
        return self.client.chat_json(
            stage="test_failure_analysis",
            messages=test_failure_messages(payload),
            max_tokens=1200,
        )

    def load_file_candidates(self) -> List[FileCandidate]:
        return [FileCandidate(path=file_id, summary="") for file_id in list_gcc_source_files(self.cfg.paths.gcc_repo_dir)]

    def load_or_create_file_summary(self, file_id: str) -> str:
        path = file_doc_path(self.cfg.paths.docs_file_dir, file_id)
        doc = load_json_if_exists(path)
        if isinstance(doc, dict):
            summary = str(doc.get("summary") or "").strip()
            if summary:
                return summary
        doc = build_file_doc(self.cfg.paths.gcc_repo_dir, file_id, max_chars=self.cfg.soapfl.file_summary_chars)
        write_json(path, doc)
        return str(doc.get("summary") or "")

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
                include_summaries=False,
            )
            response = self.client.chat_json(
                stage=f"search_suspicious_files_chunk_{index}",
                messages=suspicious_files_messages(payload),
                max_tokens=1000,
            )
            chunk_selected.extend(parse_file_selection(response, {item.path for item in chunk}))

        if chunk_selected:
            shortlist_paths = unique_keep_order(item["path"] for item in chunk_selected)
            candidate_by_path = {item.path: item for item in candidates}
            final_candidates = [candidate_by_path[path] for path in shortlist_paths if path in candidate_by_path]
        else:
            final_candidates = candidates

        final_candidates = self.with_file_summaries(final_candidates)
        payload = self.file_search_payload(
            ctx=ctx,
            behavior=behavior,
            failure=failure,
            candidates=final_candidates,
            requested_count=self.cfg.soapfl.file_top_n,
            include_summaries=True,
        )
        response = self.client.chat_json(
            stage="search_suspicious_files_final",
            messages=suspicious_files_messages(payload),
            max_tokens=1400,
        )
        selected = unique_file_items(parse_file_selection(response, {item.path for item in final_candidates}))
        if len(selected) < self.cfg.soapfl.file_top_n:
            selected_paths = {item["path"] for item in selected}
            for candidate in final_candidates:
                if candidate.path in selected_paths:
                    continue
                selected.append({"path": candidate.path, "reason": "Fallback selection due to incomplete model output."})
                selected_paths.add(candidate.path)
                if len(selected) >= min(self.cfg.soapfl.file_top_n, len(final_candidates)):
                    break
        return selected[: self.cfg.soapfl.file_top_n]

    def with_file_summaries(self, candidates: List[FileCandidate]) -> List[FileCandidate]:
        return [
            FileCandidate(path=candidate.path, summary=candidate.summary or self.load_or_create_file_summary(candidate.path))
            for candidate in candidates
        ]

    def file_search_payload(
        self,
        *,
        ctx: BugContext,
        behavior: Dict[str, Any],
        failure: Dict[str, Any],
        candidates: List[FileCandidate],
        requested_count: int,
        include_summaries: bool,
    ) -> Dict[str, Any]:
        return {
            "bug_id": ctx.bug_id,
            "failed_reproducer": "GCC compiler bug reproducer from the bug report",
            "bug_report": ctx.bug_report,
            "test_behavior": behavior,
            "test_failure_causes": failure,
            "requested_count": requested_count,
            "candidate_source_files_list": file_table(candidates, include_summaries=include_summaries),
        }

    def run_method_level_localization(
        self,
        ctx: BugContext,
        behavior: Dict[str, Any],
        failure: Dict[str, Any],
        selected_files: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        method_store = MethodStore(
            gcc_repo_dir=self.cfg.paths.gcc_repo_dir,
            docs_method_dir=self.cfg.paths.docs_method_dir,
            max_body_chars=max(self.cfg.soapfl.method_review_code_chars, 12000),
        )
        file_docs: Dict[str, str] = {}
        methods_by_file: Dict[str, List[MethodCandidate]] = {}
        related_methods: List[Dict[str, Any]] = []

        for file_item in selected_files:
            file_id = str(file_item.get("path") or "")
            if not file_id:
                continue
            file_docs[file_id] = self.load_or_create_file_summary(file_id)
            methods = method_store.load_methods_for_file(file_id)
            methods_by_file[file_id] = methods
            related_methods.extend(
                self.run_find_related_methods(
                    ctx=ctx,
                    behavior=behavior,
                    failure=failure,
                    file_id=file_id,
                    file_doc=file_docs[file_id],
                    methods=methods,
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
            related_methods=related_methods,
            file_docs=file_docs,
        )
        return {
            "input_files": [item["path"] for item in selected_files],
            "method_summary_source": "leading_explanatory_comments_only",
            "methods_by_file": {
                file_id: [method.to_report_dict(source="candidate") for method in methods]
                for file_id, methods in methods_by_file.items()
            },
            "related_methods": related_methods,
            "reviewed_methods": reviewed_methods,
        }

    def run_find_related_methods(
        self,
        *,
        ctx: BugContext,
        behavior: Dict[str, Any],
        failure: Dict[str, Any],
        file_id: str,
        file_doc: str,
        methods: List[MethodCandidate],
    ) -> List[Dict[str, Any]]:
        if not methods:
            return []
        selected: List[Dict[str, Any]] = []
        for index, chunk in enumerate(chunked(methods, self.cfg.soapfl.method_search_chunk_size), start=1):
            payload = {
                "bug_id": ctx.bug_id,
                "failed_reproducer": "GCC compiler bug reproducer from the bug report",
                "bug_report": ctx.bug_report,
                "test_behavior": behavior,
                "test_failure_causes": failure,
                "file_path": file_id,
                "file_documentation": file_doc,
                "methods_list": methods_list_table(chunk, comment_chars=350),
                "requested_count": self.cfg.soapfl.method_search_chunk_select_n,
            }
            response = self.client.chat_json(
                stage=f"find_related_methods:{short_stage_name(file_id)}:{index}",
                messages=related_methods_messages(payload),
                max_tokens=self.cfg.soapfl.method_selection_max_tokens,
            )
            selected.extend(parse_method_selection(response, {method.id for method in chunk}))

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
        related_methods: List[Dict[str, Any]],
        file_docs: Dict[str, str],
    ) -> List[Dict[str, Any]]:
        candidate_methods: List[MethodCandidate] = []
        reason_by_id: Dict[str, str] = {}
        for item in related_methods:
            method_id = str(item.get("id") or "")
            method = method_index.get(method_id)
            if method is None:
                continue
            candidate_methods.append(method)
            reason_by_id[method.id] = str(item.get("reason") or "").strip()

        reviewed: List[Dict[str, Any]] = []
        for batch_index, batch in enumerate(chunked(candidate_methods, self.cfg.soapfl.method_review_batch_size), start=1):
            payload = {
                "bug_id": ctx.bug_id,
                "failed_reproducer": "GCC compiler bug reproducer from the bug report",
                "bug_report": ctx.bug_report,
                "test_behavior": behavior,
                "test_failure_causes": failure,
                "suspicious_methods": [
                    {
                        **method.to_prompt_dict(
                            include_body=True,
                            body_chars=self.cfg.soapfl.method_review_code_chars,
                        ),
                        "file_documentation": file_docs.get(method.file, ""),
                        "related_selection_reason": reason_by_id.get(method.id, ""),
                    }
                    for method in batch
                ],
            }
            response = self.client.chat_json(
                stage=f"method_review_batch_{batch_index}",
                messages=batch_method_review_messages(payload),
                max_tokens=self.cfg.soapfl.method_review_max_tokens,
            )
            scores = parse_method_review_scores(response, {method.id for method in batch})
            for method in batch:
                item = scores.get(method.id, {})
                reviewed.append(
                    method.to_report_dict(
                        score=clamp_score(item.get("score")),
                        reason=str(item.get("reason") or "").strip(),
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


def file_table(candidates: List[FileCandidate], *, include_summaries: bool) -> str:
    if not include_summaries:
        return markdown_table(["Index", "Path"], [[str(index), item.path] for index, item in enumerate(candidates, start=1)])
    rows = [[str(index), item.path, item.summary] for index, item in enumerate(candidates, start=1)]
    return markdown_table(["Index", "Path", "File Documentation"], rows)


def methods_list_table(methods: List[MethodCandidate], *, comment_chars: int = 350) -> str:
    rows = []
    for index, method in enumerate(methods, start=1):
        rows.append(
            [
                str(index),
                method.id,
                method.qualified_name,
                f"{method.start_line}-{method.end_line}",
                method.summary[:comment_chars],
            ]
        )
    return markdown_table(["Index", "Method ID", "Method Full Name", "Lines", "Existing Comment"], rows)


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


def parse_method_review_scores(response: Dict[str, Any], allowed: set[str]) -> Dict[str, Dict[str, Any]]:
    methods = response.get("methods") if isinstance(response, dict) else []
    out: Dict[str, Dict[str, Any]] = {}
    if not isinstance(methods, list):
        return out
    for item in methods:
        if not isinstance(item, dict):
            continue
        method_id = str(item.get("id") or "").strip()
        if method_id in allowed:
            out[method_id] = {
                "score": clamp_score(item.get("score")),
                "reason": str(item.get("reason") or "").strip(),
            }
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


def clamp_score(value: Any) -> int:
    try:
        return max(0, min(10, int(value)))
    except Exception:
        return 0


def short_stage_name(value: str) -> str:
    text = "".join(ch if ch.isalnum() else "_" for ch in str(value or "stage"))
    return text[:80] or "stage"
