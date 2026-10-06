from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict

from src.common.git import checkout_ref, clean_worktree, resolve_checkout_ref
from src.common.json_io import load_json_if_exists, write_json
from src.common.llm import LLMClient
from src.evidence.extractor import EvidenceExtractor
from src.localize.agent import LocalizationAgent
from src.localize.inputs import load_issue, load_reproducer
from src.localize.repo import RepoEnumerator
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
    rust_repo_dir: Path
    file_doc_dir: Path


@dataclass(frozen=True)
class PipelineOptions:
    repro_max_chars: int = 24000
    issue_max_chars: int = 24000
    stage3_file_eval_top_k: int = 10
    candidate_agent_max_tokens: int = 5000


class LocalizationPipeline:
    def __init__(self, *, paths: PipelinePaths, client: LLMClient, options: PipelineOptions) -> None:
        self.paths = paths
        self.client = client
        self.options = options
        self.agent = LocalizationAgent(client=client, candidate_max_tokens=options.candidate_agent_max_tokens)
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

        issue = timed_value(
            stage_times,
            "load_issue",
            lambda: load_issue(self.paths.issue_dir, bug_id, max_chars=self.options.issue_max_chars),
        )
        reproducer = timed_value(
            stage_times,
            "load_reproducer",
            lambda: load_reproducer(self.paths.dataset_dir, bug_id, max_chars=self.options.repro_max_chars),
        )
        evidence = timed_value(
            stage_times,
            "load_evidence",
            lambda: self.load_or_extract_evidence(
                bug_id=bug_id,
                toolchain=checkout_ref_or_toolchain,
                build_args=build_args,
            ),
        )

        enumerator = RepoEnumerator(rust_repo_dir=self.paths.rust_repo_dir)
        doc_store = DocStore(file_doc_dir=self.paths.file_doc_dir)
        available_files = timed_value(stage_times, "enumerate_all_files", enumerator.enumerate_files)
        file_docs = timed_value(stage_times, "load_all_file_docs", lambda: doc_store.load_file_docs(available_files))
        file_result = timed_value(
            stage_times,
            "file_localization_llm",
            lambda: self.agent.run_file_localization(
                issue=issue,
                reproducer=reproducer,
                evidence=evidence,
                file_docs=file_docs,
                available_files=available_files,
                max_candidates=min(self.options.stage3_file_eval_top_k, len(available_files)),
            ),
        )
        selected_file_ids = file_result.ids()

        report = {
            "bug_id": bug_id,
            "checkout": {"requested": checkout_ref_or_toolchain, "resolved": resolved_checkout},
            "final_top10": selected_file_ids[: self.options.stage3_file_eval_top_k],
            "evidence": evidence,
            "stage1": {
                "skipped": True,
                "ablation": "w/o crate and module localization",
                "selected_crates": [],
            },
            "stage2": {
                "skipped": True,
                "ablation": "w/o crate and module localization",
                "selected_modules": [],
            },
            "stage3": {
                "candidate_universe_size": len(available_files),
                "localization_mode": "global_file_localization",
                "doc_screened_files": file_result.to_dict()["candidates"],
                "file_eval_top_k": self.options.stage3_file_eval_top_k,
            },
            "efficiency": {
                "total_wall_time_sec": round(time.time() - total_start, 3),
                "stage_times_sec": {key: round(value, 3) for key, value in stage_times.items()},
                "llm_usage": self.client.usage_tracker.to_dict(start_index=llm_start_index),
            },
        }
        attach_diagnosis(report, groundtruth_dir=self.paths.groundtruth_dir)
        write_report(self.paths.report_dir, report)
        return report

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
        rust_repo_dir=path_cfg["rust_repo_dir"],
        file_doc_dir=path_cfg["file_doc_dir"],
    )
    options = PipelineOptions(
        candidate_agent_max_tokens=int(localize_cfg.get("candidate_agent_max_tokens", 5000)),
        issue_max_chars=int(localize_cfg.get("issue_max_chars", 24000)),
        repro_max_chars=int(localize_cfg.get("repro_max_chars", 24000)),
        stage3_file_eval_top_k=int(localize_cfg.get("stage3_file_eval_top_k", 10)),
    )
    client = LLMClient.from_config(cfg)
    return LocalizationPipeline(paths=paths, options=options, client=client)
