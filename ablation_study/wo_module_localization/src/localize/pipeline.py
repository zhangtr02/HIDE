from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from src.common.git import checkout_ref, clean_worktree, resolve_checkout_ref
from src.common.json_io import load_json_if_exists, write_json
from src.evidence.extractor import EvidenceExtractor
from src.common.llm import LLMClient
from src.localize.agent import LocalizationAgent
from src.localize.inputs import load_issue, load_reproducer
from src.localize.methods import MethodCandidate, MethodStore
from src.localize.model import Candidate, CandidateResult
from src.localize.repo import RepoEnumerator, unique
from src.localize.report import attach_diagnosis, write_report
from src.localize.stores import DocStore


@dataclass(frozen=True)
class PipelinePaths:
    dataset_dir: Path
    issue_dir: Path
    log_dir: Path
    query_trace_dir: Path
    evidence_dir: Path
    report_dir: Path
    groundtruth_dir: Path
    method_groundtruth_dir: Path
    rust_repo_dir: Path
    crate_doc_dir: Path
    module_doc_dir: Path
    file_doc_dir: Path


@dataclass(frozen=True)
class PipelineOptions:
    repro_max_chars: int = 24000
    issue_max_chars: int = 24000
    max_code_chars: int = 12000
    stage1_crate_count: int = 4
    stage2_module_count: int = 12
    stage3_file_per_module_count: int = 4
    stage3_root_file_per_crate_count: int = 2
    stage3_file_merge_count: int = 10
    stage3_file_eval_top_k: int = 10
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
        self.evidence_extractor = EvidenceExtractor(log_dir=paths.log_dir, query_trace_dir=paths.query_trace_dir)

    def run_bug(
        self,
        *,
        bug_id: str,
        checkout_ref_or_toolchain: str = "",
        build_args: str = "",
        clean_checkout: bool = False,
    ) -> Dict[str, Any]:
        total_start = time.time()
        stage_times: Dict[str, float] = {}
        llm_start_index = len(self.client.usage_tracker.calls)
        resolved_checkout = ""

        if checkout_ref_or_toolchain.strip():
            def do_checkout() -> str:
                resolved = resolve_checkout_ref(self.paths.rust_repo_dir, checkout_ref_or_toolchain)
                if clean_checkout:
                    clean_worktree(self.paths.rust_repo_dir)
                checkout_ref(self.paths.rust_repo_dir, resolved, force=clean_checkout)
                return resolved

            resolved_checkout = timed_value(stage_times, "checkout", do_checkout)

        issue = timed_value(stage_times, "load_issue", lambda: load_issue(self.paths.issue_dir, bug_id, max_chars=self.options.issue_max_chars))
        reproducer = timed_value(
            stage_times,
            "load_reproducer",
            lambda: load_reproducer(self.paths.dataset_dir, bug_id, max_chars=self.options.repro_max_chars),
        )
        evidence = timed_value(
            stage_times,
            "load_evidence",
            lambda: self.load_or_extract_evidence(bug_id=bug_id, toolchain=checkout_ref_or_toolchain, build_args=build_args),
        )

        enumerator = RepoEnumerator(rust_repo_dir=self.paths.rust_repo_dir)
        doc_store = DocStore(
            crate_doc_dir=self.paths.crate_doc_dir,
            module_doc_dir=self.paths.module_doc_dir,
            file_doc_dir=self.paths.file_doc_dir,
            rust_repo_dir=self.paths.rust_repo_dir,
        )
        method_store = MethodStore(rust_repo_dir=self.paths.rust_repo_dir, max_body_chars=self.options.max_code_chars)

        available_crates = timed_value(stage_times, "enumerate_stage1_crates", enumerator.enumerate_crates)
        crate_docs = timed_value(stage_times, "load_crate_docs", lambda: doc_store.load_crate_docs(available_crates))
        stage1 = timed_value(
            stage_times,
            "stage1_llm",
            lambda: self.agent.run_stage1(
                issue=issue,
                reproducer=reproducer,
                evidence=evidence,
                crate_docs=crate_docs,
                available_crates=available_crates,
                max_candidates=min(self.options.stage1_crate_count, len(available_crates)),
            ),
        )

        selected_crates = stage1.ids()
        available_modules = timed_value(
            stage_times,
            "enumerate_all_modules_in_selected_crates",
            lambda: unique(enumerator.enumerate_modules(selected_crates)),
        )
        stage2 = CandidateResult()
        selected_modules = available_modules
        stage2_artifacts = {
            "mode": "skipped_module_localization_all_modules_in_selected_crates",
            "available_modules": available_modules,
            "selection_count": 0,
            "final_result": stage2,
        }
        stage3_artifacts = timed_value(
            stage_times,
            "stage3_file_doc_llm",
            lambda: self.run_grouped_stage3_doc_screening(
                enumerator=enumerator,
                doc_store=doc_store,
                issue=issue,
                reproducer=reproducer,
                evidence=evidence,
                stage1_result=stage1,
                stage2_result=stage2,
                selected_crates=selected_crates,
                selected_modules=selected_modules,
            ),
        )
        selected_file_ids = stage3_artifacts["final_result"].ids()
        method_input_files = selected_file_ids[: self.options.stage4_method_input_file_count]
        method_artifacts = timed_value(
            stage_times,
            "stage4_method_llm",
            lambda: self.run_stage4_method_localization(
                method_store=method_store,
                issue=issue,
                reproducer=reproducer,
                evidence=evidence,
                stage1_result=stage1,
                stage2_result=stage2,
                stage3_file_selection=stage3_artifacts["final_result"],
                candidate_files=method_input_files,
            ),
        )
        ranked_methods = method_artifacts["ranked_result"]

        report = {
            "bug_id": bug_id,
            "checkout": {"requested": checkout_ref_or_toolchain, "resolved": resolved_checkout},
            "final_top10": selected_file_ids[: self.options.stage3_file_eval_top_k],
            "final_method_top10": ranked_methods.top_method_ids(self.options.stage4_method_final_top_k),
            "evidence": evidence,
            "stage1": {
                "candidate_universe_size": len(available_crates),
                "selected_crates": stage1.to_dict()["candidates"],
            },
            "stage2": {
                "skipped": True,
                "ablation": "w/o module localization",
                "candidate_universe_size": len(available_modules),
                "selection_mode": stage2_artifacts["mode"],
                "selected_modules": [],
                "expanded_modules_for_stage3_count": len(selected_modules),
                "note": "Stage2 module localization is skipped. Stage3 receives all modules under Stage1-selected crates.",
            },
            "stage3": {
                "candidate_universe_size": len(stage3_artifacts["available_files"]),
                "candidate_root_files": stage3_artifacts["candidate_root_files"],
                "doc_screening_mode": stage3_artifacts["mode"],
                "doc_screened_groups": stage3_artifacts["groups"],
                "doc_screened_merged_count": stage3_artifacts["merged_candidate_count"],
                "doc_screened_files": stage3_artifacts["final_result"].to_dict()["candidates"],
                "file_eval_top_k": self.options.stage3_file_eval_top_k,
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
        attach_diagnosis(report, groundtruth_dir=self.paths.groundtruth_dir, method_groundtruth_dir=self.paths.method_groundtruth_dir)
        write_report(self.paths.report_dir, report)
        return report

    def run_grouped_stage3_doc_screening(
        self,
        *,
        enumerator: RepoEnumerator,
        doc_store: DocStore,
        issue: Dict[str, Any],
        reproducer: Dict[str, Any],
        evidence: Dict[str, Any],
        stage1_result: CandidateResult,
        stage2_result: CandidateResult,
        selected_crates: list[str],
        selected_modules: list[str],
    ) -> Dict[str, Any]:
        groups = build_stage3_file_groups(enumerator=enumerator, selected_crates=selected_crates, selected_modules=selected_modules)
        candidate_root_files = unique([path for group in groups if group["type"] == "root" for path in group["files"]])
        available_files = unique([path for group in groups for path in group["files"]])
        group_reports: list[dict[str, Any]] = []
        merged = CandidateResult()

        for index, group in enumerate(groups, start=1):
            group_files = list(group["files"])
            if not group_files:
                continue
            selection_count = self.options.stage3_root_file_per_crate_count if group["type"] == "root" else self.options.stage3_file_per_module_count
            selection_count = max(1, min(selection_count, len(group_files)))
            file_docs = doc_store.load_file_docs(group_files)
            result = self.agent.run_stage3_doc_screening(
                issue=issue,
                reproducer=reproducer,
                evidence=evidence,
                stage1_result=stage1_result,
                stage2_result=stage2_result,
                candidate_root_files=group_files if group["type"] == "root" else [],
                file_docs=file_docs,
                available_files=group_files,
                max_candidates=selection_count,
                candidate_modules=[group["id"]] if group["type"] == "module" else [],
                screening_context={
                    "mode": "per_group_doc_screening",
                    "group_index": index,
                    "group_count": len(groups),
                    "group_type": group["type"],
                    "group_id": group["id"],
                    "group_candidate_count": len(group_files),
                    "selection_count": selection_count,
                },
            )
            merged = merge_candidate_results(merged, result, group_id=str(group["id"]))
            group_reports.append(
                {
                    "type": group["type"],
                    "id": group["id"],
                    "candidate_count": len(group_files),
                    "selection_count": selection_count,
                    "selected_files": result.to_dict()["candidates"],
                }
            )

        final_result = merged
        merged_candidate_count = len(merged.candidates)
        if len(merged.candidates) > self.options.stage3_file_merge_count:
            merged_files = merged.ids()
            merged_docs = doc_store.load_file_docs(merged_files)
            final_result = self.agent.run_stage3_doc_screening(
                issue=issue,
                reproducer=reproducer,
                evidence=evidence,
                stage1_result=stage1_result,
                stage2_result=stage2_result,
                candidate_root_files=[path for path in candidate_root_files if path in set(merged_files)],
                file_docs=merged_docs,
                available_files=merged_files,
                max_candidates=self.options.stage3_file_merge_count,
                candidate_modules=stage2_result.ids(),
                screening_context={
                    "mode": "global_doc_merge_after_per_group_screening",
                    "group_count": len(groups),
                    "merged_candidate_count": len(merged.candidates),
                    "selection_count": self.options.stage3_file_merge_count,
                },
            )

        return {
            "mode": "per_group_doc_screening",
            "candidate_root_files": candidate_root_files,
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
        evidence: Dict[str, Any],
        stage1_result: CandidateResult,
        stage2_result: CandidateResult,
        stage3_file_selection: CandidateResult,
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
                group_reports.append({"type": "file", "id": file_id, "candidate_count": 0, "selection_count": 0, "selected_methods": []})
                continue
            selection_count = min(self.options.stage4_method_per_file_count, len(methods))
            result = self.agent.run_stage4_method_file_selection(
                issue=issue,
                reproducer=reproducer,
                evidence=evidence,
                stage1_result=stage1_result,
                stage2_result=stage2_result,
                stage3_file_selection=stage3_file_selection,
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
            evidence=evidence,
            stage1_result=stage1_result,
            stage2_result=stage2_result,
            stage3_file_selection=stage3_file_selection,
            file_wise_method_result=file_wise_method_result,
            methods=selected_methods,
            final_top_k=self.options.stage4_method_final_top_k,
        )

        return {
            "method_input_files": method_input_files,
            "candidate_universe_size": candidate_universe_size,
            "groups": group_reports,
            "merged_candidate_count": len(selected_methods),
            "selected_methods": [method.to_report_dict(reason=reason_for_candidate(merged, method.id)) for method in selected_methods],
            "ranked_result": ranked_result,
            "ranked_methods": enrich_ranked_methods(ranked_result, method_by_id),
        }

    def load_or_extract_evidence(self, *, bug_id: str, toolchain: str, build_args: str) -> Dict[str, Any]:
        path = self.paths.evidence_dir / f"{bug_id}.json"
        obj = load_json_if_exists(path)
        if obj is not None:
            return obj
        evidence = self.evidence_extractor.extract(bug_id=bug_id, toolchain=toolchain, build_args=build_args)
        write_json(path, evidence)
        return evidence


def timed_value(stage_times: Dict[str, float], key: str, fn: Callable[[], Any]) -> Any:
    start = time.time()
    value = fn()
    stage_times[key] = time.time() - start
    return value


def build_stage3_file_groups(*, enumerator: RepoEnumerator, selected_crates: list[str], selected_modules: list[str]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for module_id in unique(selected_modules):
        files = enumerator.enumerate_files_for_modules([module_id])
        if files:
            groups.append({"type": "module", "id": module_id, "files": files})
    for crate_id in unique(selected_crates):
        files = enumerator.enumerate_root_files([crate_id])
        if files:
            groups.append({"type": "root", "id": crate_id, "files": files})
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


def build_pipeline_from_config(cfg: Dict[str, Any] | None = None) -> LocalizationPipeline:
    from src.common.config import load_config

    cfg = cfg or load_config()
    path_cfg = cfg["paths"]
    localize_cfg = cfg.get("localize") or {}
    paths = PipelinePaths(
        dataset_dir=path_cfg["dataset_dir"],
        issue_dir=path_cfg["issue_dir"],
        log_dir=path_cfg["log_dir"],
        query_trace_dir=path_cfg["query_trace_dir"],
        evidence_dir=path_cfg["evidence_dir"],
        report_dir=path_cfg["report_dir"],
        groundtruth_dir=path_cfg["groundtruth_dir"],
        method_groundtruth_dir=path_cfg["method_groundtruth_dir"],
        rust_repo_dir=path_cfg["rust_repo_dir"],
        crate_doc_dir=path_cfg["crate_doc_dir"],
        module_doc_dir=path_cfg["module_doc_dir"],
        file_doc_dir=path_cfg["file_doc_dir"],
    )
    options = PipelineOptions(
        candidate_agent_max_tokens=int(localize_cfg.get("candidate_agent_max_tokens", 5000)),
        code_agent_max_tokens=int(localize_cfg.get("code_agent_max_tokens", 7000)),
        max_code_chars=int(localize_cfg.get("max_code_chars", 12000)),
        issue_max_chars=int(localize_cfg.get("issue_max_chars", 24000)),
        repro_max_chars=int(localize_cfg.get("repro_max_chars", 24000)),
        stage1_crate_count=int(localize_cfg.get("stage1_crate_count", 4)),
        stage2_module_count=int(localize_cfg.get("stage2_module_count", 12)),
        stage3_file_per_module_count=int(localize_cfg.get("stage3_file_per_module_count", 4)),
        stage3_root_file_per_crate_count=int(localize_cfg.get("stage3_root_file_per_crate_count", 2)),
        stage3_file_merge_count=int(localize_cfg.get("stage3_file_merge_count", 10)),
        stage3_file_eval_top_k=int(localize_cfg.get("stage3_file_eval_top_k", 10)),
        stage4_method_input_file_count=int(localize_cfg.get("stage4_method_input_file_count", 10)),
        stage4_method_per_file_count=int(localize_cfg.get("stage4_method_per_file_count", 5)),
        stage4_method_final_top_k=int(localize_cfg.get("stage4_method_final_top_k", 10)),
    )
    client = LLMClient.from_config(cfg)
    return LocalizationPipeline(paths=paths, options=options, client=client)
