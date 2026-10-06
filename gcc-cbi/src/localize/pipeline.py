from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from src.common.git import checkout_ref, clean_worktree, resolve_checkout_ref
from src.common.json_io import load_json, write_json
from src.common.llm import LLMClient
from src.evidence.extractor import EvidenceExtractor, normalize_evidence_schema
from src.localize.agent import LocalizationAgent
from src.localize.inputs import load_issue, load_reproducer
from src.localize.methods import MethodCandidate, MethodStore
from src.localize.model import BugInput, Candidate, CandidateResult
from src.localize.repo import RepoEnumerator, unique
from src.localize.report import attach_diagnosis, write_report
from src.localize.stores import DocStore


@dataclass(frozen=True)
class PipelinePaths:
    dataset_csv: Path
    issue_dir: Path
    reproducer_dir: Path
    pass_trace_dir: Path
    evidence_dir: Path
    report_dir: Path
    groundtruth_dir: Path
    method_groundtruth_dir: Path
    gcc_repo_dir: Path
    module_doc_dir: Path
    file_doc_dir: Path


@dataclass(frozen=True)
class PipelineOptions:
    issue_max_chars: int = 24000
    reproducer_max_chars: int = 24000
    pass_trace_max_items: int = 200
    stage1_module_count: int = 4
    stage2_file_per_module_count: int = 4
    stage2_file_merge_count: int = 10
    stage2_file_eval_top_k: int = 10
    stage4_method_input_file_count: int = 10
    stage4_method_per_file_count: int = 5
    stage4_method_final_top_k: int = 10
    candidate_agent_max_tokens: int = 5000
    code_agent_max_tokens: int = 7000


class LocalizationPipeline:
    def __init__(self, *, paths: PipelinePaths, client: LLMClient, options: PipelineOptions) -> None:
        self.paths = paths
        self.client = client
        self.options = options
        self.agent = LocalizationAgent(
            client=client,
            candidate_max_tokens=options.candidate_agent_max_tokens,
            code_max_tokens=options.code_agent_max_tokens,
        )
        self.evidence_extractor = EvidenceExtractor(
            pass_trace_dir=paths.pass_trace_dir,
        )

    def run_bug(self, *, bug_id: str, checkout_ref_or_commit: str = "", clean_checkout: bool = False) -> Dict[str, Any]:
        total_start = time.time()
        stage_times: Dict[str, float] = {}
        llm_start_index = len(self.client.usage_tracker.calls)
        resolved_checkout = ""

        if checkout_ref_or_commit.strip():
            def do_checkout() -> str:
                resolved = resolve_checkout_ref(self.paths.gcc_repo_dir, checkout_ref_or_commit)
                if clean_checkout:
                    clean_worktree(self.paths.gcc_repo_dir)
                checkout_ref(self.paths.gcc_repo_dir, resolved, force=clean_checkout)
                return resolved

            resolved_checkout = timed_value(stage_times, "checkout", do_checkout)

        issue = timed_value(
            stage_times,
            "load_issue",
            lambda: load_issue(self.paths.issue_dir, bug_id, max_chars=self.options.issue_max_chars),
        )
        reproducer = timed_value(
            stage_times,
            "load_reproducer",
            lambda: load_reproducer(self.paths.reproducer_dir, bug_id, max_chars=self.options.reproducer_max_chars),
        )
        evidence = timed_value(
            stage_times,
            "load_evidence",
            lambda: self.load_or_extract_evidence(
                bug_id=bug_id,
            ),
        )
        bug = BugInput(
            bug_id=bug_id,
            issue=issue,
            reproducer=reproducer,
            execution=evidence.get("execution") if isinstance(evidence.get("execution"), dict) else {"instance_id": bug_id, "available": False},
            log=evidence.get("log") if isinstance(evidence.get("log"), dict) else {"instance_id": bug_id, "available": False},
            pass_trace=evidence.get("pass_trace") if isinstance(evidence.get("pass_trace"), dict) else {"instance_id": bug_id, "available": False},
        )

        enumerator = RepoEnumerator(gcc_repo_dir=self.paths.gcc_repo_dir)
        doc_store = DocStore(
            module_doc_dir=self.paths.module_doc_dir,
            file_doc_dir=self.paths.file_doc_dir,
            gcc_repo_dir=self.paths.gcc_repo_dir,
        )
        method_store = MethodStore(gcc_repo_dir=self.paths.gcc_repo_dir)

        available_modules = timed_value(stage_times, "enumerate_stage1_modules", enumerator.enumerate_modules)
        module_docs = timed_value(stage_times, "load_module_docs", lambda: doc_store.load_module_docs(available_modules))
        stage1 = timed_value(
            stage_times,
            "stage1_llm",
            lambda: self.agent.run_stage1(
                issue=bug.issue,
                reproducer=bug.reproducer,
                execution=bug.execution,
                log=bug.log,
                pass_trace=bug.pass_trace,
                module_docs=module_docs,
                available_modules=available_modules,
                max_candidates=min(self.options.stage1_module_count, len(available_modules)),
            ),
        )

        selected_modules = stage1.ids()
        stage2_artifacts = timed_value(
            stage_times,
            "stage2_file_doc_llm",
            lambda: self.run_stage2_file_screening(
                enumerator=enumerator,
                doc_store=doc_store,
                issue=bug.issue,
                reproducer=bug.reproducer,
                execution=bug.execution,
                log=bug.log,
                pass_trace=bug.pass_trace,
                stage1_result=stage1,
                selected_modules=selected_modules,
            ),
        )
        selected_file_ids = stage2_artifacts["final_result"].ids()
        method_input_files = selected_file_ids[: self.options.stage4_method_input_file_count]
        method_artifacts = timed_value(
            stage_times,
            "stage4_method_llm",
            lambda: self.run_stage4_method_localization(
                method_store=method_store,
                issue=bug.issue,
                reproducer=bug.reproducer,
                execution=bug.execution,
                log=bug.log,
                pass_trace=bug.pass_trace,
                stage1_result=stage1,
                stage2_file_selection=stage2_artifacts["final_result"],
                candidate_files=method_input_files,
            ),
        )
        ranked_methods = method_artifacts["ranked_result"]

        report = {
            "bug_id": bug_id,
            "checkout": {
                "requested": checkout_ref_or_commit,
                "resolved": resolved_checkout,
            },
            "final_top10": selected_file_ids[: self.options.stage2_file_eval_top_k],
            "final_method_top10": ranked_methods.top_method_ids(self.options.stage4_method_final_top_k),
            "evidence": evidence,
            "stage1": {
                "candidate_universe_size": len(available_modules),
                "selected_modules": stage1.to_dict()["candidates"],
            },
            "stage2": {
                "candidate_universe_size": len(stage2_artifacts["available_files"]),
                "selection_mode": stage2_artifacts["mode"],
                "doc_screened_groups": stage2_artifacts["groups"],
                "doc_screened_merged_count": stage2_artifacts["merged_candidate_count"],
                "selected_files": stage2_artifacts["final_result"].to_dict()["candidates"],
            },
            "stage3": {
                "candidate_universe_size": len(stage2_artifacts["available_files"]),
                "doc_screening_mode": stage2_artifacts["mode"],
                "doc_screened_groups": stage2_artifacts["groups"],
                "doc_screened_merged_count": stage2_artifacts["merged_candidate_count"],
                "doc_screened_files": stage2_artifacts["final_result"].to_dict()["candidates"],
                "file_top10": selected_file_ids[: self.options.stage2_file_eval_top_k],
                "file_eval_top_k": self.options.stage2_file_eval_top_k,
                "method_input_files": method_input_files,
            },
            "stage4": {
                "candidate_universe_size": method_artifacts["candidate_universe_size"],
                "method_input_files": method_artifacts["method_input_files"],
                "method_groups": method_artifacts["groups"],
                "selected_merged_count": method_artifacts["merged_candidate_count"],
                "selected_methods": method_artifacts["selected_methods"],
                "ranked_methods": method_artifacts["ranked_methods"],
            },
            "efficiency": {
                "total_wall_time_sec": round(time.time() - total_start, 3),
                "stage_times_sec": {key: round(value, 3) for key, value in stage_times.items()},
                "llm_usage": self.client.usage_tracker.to_dict(start_index=llm_start_index),
            },
        }
        attach_diagnosis(
            report,
            groundtruth_dir=self.paths.groundtruth_dir,
            method_groundtruth_dir=self.paths.method_groundtruth_dir,
        )
        write_report(self.paths.report_dir, report)
        return report

    def load_or_extract_evidence(self, *, bug_id: str) -> Dict[str, Any]:
        path = self.paths.evidence_dir / f"{bug_id}.json"
        if path.exists():
            loaded = load_json(path)
            normalized = normalize_evidence_schema(loaded, bug_id=bug_id)
            if normalized != loaded:
                write_json(path, normalized)
            return normalized
        evidence = self.evidence_extractor.extract(
            bug_id=bug_id,
            pass_trace_max_items=self.options.pass_trace_max_items,
        )
        write_json(path, evidence)
        return evidence


    def run_stage2_file_screening(
        self,
        *,
        enumerator: RepoEnumerator,
        doc_store: DocStore,
        issue: Dict[str, Any],
        reproducer: Dict[str, Any],
        execution: Dict[str, Any],
        log: Dict[str, Any],
        pass_trace: Dict[str, Any],
        stage1_result: CandidateResult,
        selected_modules: list[str],
    ) -> Dict[str, Any]:
        groups = build_file_groups(enumerator=enumerator, selected_modules=selected_modules)
        available_files = unique([path for group in groups for path in group["files"]])
        group_reports: list[dict[str, Any]] = []
        merged = CandidateResult()

        for index, group in enumerate(groups, start=1):
            group_files = list(group["files"])
            if not group_files:
                continue
            selection_limit = max(1, min(self.options.stage2_file_per_module_count, len(group_files)))
            file_docs = doc_store.load_file_docs(group_files)
            screening_mode = "per_module_doc_screening"
            result = self.agent.run_stage2_file_screening(
                issue=issue,
                reproducer=reproducer,
                execution=execution,
                log=log,
                pass_trace=pass_trace,
                stage1_result=stage1_result,
                candidate_modules=[group["id"]],
                file_docs=file_docs,
                available_files=group_files,
                max_candidates=selection_limit,
                screening_context={
                    "mode": screening_mode,
                    "group_index": index,
                    "group_count": len(groups),
                    "group_id": group["id"],
                    "group_candidate_count": len(group_files),
                    "selection_limit": selection_limit,
                    "file_doc_mode": "available",
                },
            )
            merged = merge_candidate_results(merged, result, group_id=str(group["id"]))
            group_reports.append(
                {
                    "type": "module",
                    "id": group["id"],
                    "candidate_count": len(group_files),
                    "selection_limit": selection_limit,
                    "screening_mode": screening_mode,
                    "file_doc_mode": "available",
                    "selected_files": result.to_dict()["candidates"],
                }
            )

        final_result = merged
        merged_candidate_count = len(merged.candidates)
        if len(merged.candidates) > self.options.stage2_file_merge_count:
            merged_files = merged.ids()
            merged_docs = doc_store.load_file_docs(merged_files)
            final_result = self.agent.run_stage2_file_screening(
                issue=issue,
                reproducer=reproducer,
                execution=execution,
                log=log,
                pass_trace=pass_trace,
                stage1_result=stage1_result,
                candidate_modules=selected_modules,
                file_docs=merged_docs,
                available_files=merged_files,
                max_candidates=self.options.stage2_file_merge_count,
                screening_context={
                    "mode": "global_doc_merge_after_per_module_screening",
                    "group_count": len(groups),
                    "merged_candidate_count": len(merged.candidates),
                    "selection_limit": self.options.stage2_file_merge_count,
                },
            )

        return {
            "mode": "per_module_doc_screening",
            "available_files": available_files,
            "groups": group_reports,
            "merged_candidate_count": merged_candidate_count,
            "final_result": final_result,
        }

    def run_stage4_method_localization(
        self,
        *,
        method_store: MethodStore,
        issue: Dict[str, Any],
        reproducer: Dict[str, Any],
        execution: Dict[str, Any],
        log: Dict[str, Any],
        pass_trace: Dict[str, Any],
        stage1_result: CandidateResult,
        stage2_file_selection: CandidateResult,
        candidate_files: list[str],
    ) -> Dict[str, Any]:
        method_input_files = unique(candidate_files)
        method_by_id: dict[str, MethodCandidate] = {}
        group_reports: list[dict[str, Any]] = []
        merged = CandidateResult()
        candidate_universe_size = 0

        for index, file_id in enumerate(method_input_files, start=1):
            methods = method_store.load_methods_for_file(file_id)
            candidate_universe_size += len(methods)
            for method in methods:
                method_by_id[method.id] = method
            if not methods:
                group_reports.append({"type": "file", "id": file_id, "group_index": index, "candidate_count": 0, "selection_count": 0, "selected_methods": []})
                continue
            selection_count = min(self.options.stage4_method_per_file_count, len(methods))
            result = self.agent.run_stage4_method_file_selection(
                issue=issue,
                reproducer=reproducer,
                execution=execution,
                log=log,
                pass_trace=pass_trace,
                stage1_result=stage1_result,
                stage2_file_selection=stage2_file_selection,
                file_id=file_id,
                methods=methods,
                max_candidates=selection_count,
            )
            merged = merge_candidate_results(merged, result, group_id=file_id)
            group_reports.append(
                {
                    "type": "file",
                    "id": file_id,
                    "group_index": index,
                    "candidate_count": len(methods),
                    "selection_count": selection_count,
                    "selected_methods": enrich_method_candidates(result, method_by_id),
                }
            )

        selected_methods = [method_by_id[method_id] for method_id in merged.ids() if method_id in method_by_id]
        file_wise_method_result = {"groups": group_reports, "merged_candidate_count": len(selected_methods)}
        ranked_result = self.agent.run_stage4_method_global_rerank(
            issue=issue,
            reproducer=reproducer,
            execution=execution,
            log=log,
            pass_trace=pass_trace,
            stage1_result=stage1_result,
            stage2_file_selection=stage2_file_selection,
            file_wise_method_result=file_wise_method_result,
            methods=selected_methods,
            final_top_k=self.options.stage4_method_final_top_k,
        )
        ranked_methods = enrich_ranked_methods(ranked_result, method_by_id)

        return {
            "method_input_files": method_input_files,
            "candidate_universe_size": candidate_universe_size,
            "groups": group_reports,
            "merged_candidate_count": len(selected_methods),
            "selected_methods": [method.to_report_dict(reason=reason_for_candidate(merged, method.id)) for method in selected_methods],
            "ranked_result": ranked_result,
            "ranked_methods": ranked_methods,
        }


def timed_value(stage_times: Dict[str, float], key: str, fn: Callable[[], Any]) -> Any:
    start = time.time()
    value = fn()
    stage_times[key] = time.time() - start
    return value


def build_file_groups(*, enumerator: RepoEnumerator, selected_modules: list[str]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for module_id in unique(selected_modules):
        files = enumerator.enumerate_files_for_modules([module_id])
        if files:
            groups.append({"type": "module", "id": module_id, "files": files})
    return groups


def merge_candidate_results(base: CandidateResult, extra: CandidateResult, *, group_id: str) -> CandidateResult:
    candidates = list(base.candidates)
    seen = {candidate.id for candidate in candidates}
    for candidate in extra.candidates:
        if candidate.id in seen:
            continue
        seen.add(candidate.id)
        candidates.append(Candidate(id=candidate.id, reason=f"[{group_id}] {candidate.reason}"))
    return CandidateResult(candidates=candidates)


def reason_for_candidate(result: CandidateResult, candidate_id: str) -> str:
    for candidate in result.candidates:
        if candidate.id == candidate_id:
            return candidate.reason
    return ""


def enrich_method_candidates(result: CandidateResult, method_by_id: dict[str, MethodCandidate]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for candidate in result.candidates:
        method = method_by_id.get(candidate.id)
        items.append(method.to_report_dict(reason=candidate.reason) if method else {"id": candidate.id, "reason": candidate.reason})
    return items


def enrich_ranked_methods(ranked_result: Any, method_by_id: dict[str, MethodCandidate]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for ranked in getattr(ranked_result, "ranked_methods", []):
        method = method_by_id.get(ranked.id)
        items.append(method.to_report_dict(rank=ranked.rank, reason=ranked.reason) if method else {"id": ranked.id, "rank": ranked.rank, "reason": ranked.reason})
    return items


def build_pipeline_from_config() -> LocalizationPipeline:
    from src.common.config import load_config

    cfg = load_config()
    path_cfg = cfg["paths"]
    localize_cfg = cfg.get("localize") or {}
    paths = PipelinePaths(
        dataset_csv=path_cfg["dataset_csv"],
        issue_dir=path_cfg["issue_dir"],
        reproducer_dir=path_cfg["reproducer_dir"],
        pass_trace_dir=path_cfg["pass_trace_dir"],
        evidence_dir=path_cfg["evidence_dir"],
        report_dir=path_cfg["report_dir"],
        groundtruth_dir=path_cfg["groundtruth_dir"],
        method_groundtruth_dir=path_cfg["method_groundtruth_dir"],
        gcc_repo_dir=path_cfg["gcc_repo_dir"],
        module_doc_dir=path_cfg["module_doc_dir"],
        file_doc_dir=path_cfg["file_doc_dir"],
    )
    options = PipelineOptions(
        candidate_agent_max_tokens=int(localize_cfg.get("candidate_agent_max_tokens", 5000)),
        code_agent_max_tokens=int(localize_cfg.get("code_agent_max_tokens", 7000)),
        issue_max_chars=int(localize_cfg.get("issue_max_chars", 24000)),
        reproducer_max_chars=int(localize_cfg.get("reproducer_max_chars", 24000)),
        pass_trace_max_items=int(localize_cfg.get("pass_trace_max_items", 200)),
        stage1_module_count=int(localize_cfg.get("stage1_module_count", localize_cfg.get("stage1_module_max_candidates", 4))),
        stage2_file_per_module_count=int(localize_cfg.get("stage2_file_per_module_count", 4)),
        stage2_file_merge_count=int(localize_cfg.get("stage2_file_merge_count", localize_cfg.get("stage2_file_max_candidates", 10))),
        stage2_file_eval_top_k=int(localize_cfg.get("stage2_file_eval_top_k", 10)),
        stage4_method_input_file_count=int(localize_cfg.get("stage4_method_input_file_count", 10)),
        stage4_method_per_file_count=int(localize_cfg.get("stage4_method_per_file_count", 5)),
        stage4_method_final_top_k=int(localize_cfg.get("stage4_method_final_top_k", 10)),
    )
    from src.common.llm import LLMClient

    client = LLMClient.from_config(cfg)
    return LocalizationPipeline(paths=paths, options=options, client=client)
