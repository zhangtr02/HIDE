from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, TypeVar

from src.common.git import checkout_ref, clean_worktree, resolve_checkout_ref
from src.common.gcc_modules import SPLIT_TOP_LEVEL_MODULES, root_module_id
from src.common.json_io import load_json_if_exists, write_json
from src.docgen.code_io import read_code
from src.docgen.generator import DocGenerator
from src.docgen.model import GCCFile, GCCModule, GCCTree
from src.docgen.paths import file_doc_path, module_doc_path
from src.docgen.scanner import scan_gcc_modules

T = TypeVar("T")


@dataclass(frozen=True)
class DocgenPaths:
    gcc_repo_dir: Path
    file_doc_dir: Path
    module_doc_dir: Path


@dataclass(frozen=True)
class DocgenOptions:
    max_code_chars: int = 12000
    file_doc_batch_size: int = 64
    same_name_group_batch_size: int = 8
    same_name_file_batch_size: int = 24
    checkout: bool = True
    clean_checkout: bool = False
    level: str = "all"


class DocgenPipeline:
    def __init__(self, *, paths: DocgenPaths, generator: DocGenerator, options: DocgenOptions) -> None:
        self.paths = paths
        self.generator = generator
        self.options = options

    def run_for_commit(self, *, commit: str, progress_label: str = "") -> None:
        checkout_ref_name = commit
        if self.options.checkout:
            checkout_ref_name = resolve_checkout_ref(self.paths.gcc_repo_dir, commit)
            checkout_message = (
                f"checkout {commit}"
                if checkout_ref_name == commit
                else f"checkout {commit} -> {checkout_ref_name}"
            )
            _log(progress_label, checkout_message)
            if self.options.clean_checkout:
                clean_worktree(self.paths.gcc_repo_dir)
            checkout_ref(self.paths.gcc_repo_dir, checkout_ref_name, force=self.options.clean_checkout)

        tree = scan_gcc_modules(self.paths.gcc_repo_dir)
        new_file_ids: List[str] = []
        if self.options.level in {"all", "file"}:
            new_file_ids = self.generate_missing_file_docs(tree=tree, commit=checkout_ref_name, progress_label=progress_label)
        if self.options.level in {"all", "file", "same-name"}:
            target_file_ids = new_file_ids if self.options.level != "same-name" else [item.id for item in tree.all_files()]
            self.generate_same_name_differentiation(
                tree=tree,
                commit=checkout_ref_name,
                target_file_ids=target_file_ids,
                progress_label=progress_label,
            )

    def run_module_docs_from_existing_file_docs(self, *, progress_label: str = "") -> None:
        tree = build_tree_from_file_docs(self.paths.file_doc_dir)
        self.generate_module_docs(
            tree=tree,
            commit="accumulated-file-docs",
            progress_label=progress_label,
            refresh_stale=True,
        )

    def generate_missing_file_docs(self, *, tree: GCCTree, commit: str, progress_label: str = "") -> List[str]:
        generated_ids: List[str] = []
        modules = list(tree.modules.values())
        module_count = len(modules)
        for module_index, module in enumerate(modules, start=1):
            target_files = [item for item in module.files if not file_doc_path(self.paths.file_doc_dir, item.id).exists()]
            if not target_files:
                continue
            _log(
                progress_label,
                f"module {module_index}/{module_count} "
                f"file docs {module.id}: {len(target_files)} missing",
            )
            batches = list(_chunks(target_files, max(1, self.options.file_doc_batch_size)))
            for batch_index, batch_files in enumerate(batches, start=1):
                _log(
                    progress_label,
                    f"module {module_index}/{module_count} "
                    f"file docs {module.id}: batch {batch_index}/{len(batches)} "
                    f"({len(batch_files)} files)",
                )
                payload = {
                    "commit": commit,
                    "module_id": module.id,
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "target_files": [item.id for item in batch_files],
                    "module_file_paths": [item.id for item in module.files],
                    "sibling_files": [
                        {
                            "path": item.id,
                            "code": read_code(item.abs_path, max_chars=self.options.max_code_chars),
                        }
                        for item in batch_files
                    ],
                    "existing_docs": load_existing_file_docs(self.paths.file_doc_dir, module.files),
                }
                docs = self.generator.generate_file_docs(payload, target_ids=[item.id for item in batch_files])
                for doc in docs:
                    write_json(file_doc_path(self.paths.file_doc_dir, doc["id"]), doc)
                    generated_ids.append(doc["id"])
        return generated_ids

    def generate_same_name_differentiation(
        self,
        *,
        tree: GCCTree,
        commit: str,
        target_file_ids: Iterable[str],
        progress_label: str = "",
    ) -> None:
        target_file_set = {file_id for file_id in target_file_ids if file_id}
        if not target_file_set:
            return

        modules = list(tree.modules.values())
        module_count = len(modules)
        for module_index, module in enumerate(modules, start=1):
            same_name_groups = _same_name_files(module.files)
            if not same_name_groups:
                continue

            group_file_ids = {file_id for ids in same_name_groups.values() for file_id in ids}
            module_target_ids = group_file_ids & target_file_set
            if not module_target_ids:
                continue

            group_files = [item for item in module.files if item.id in group_file_ids]
            existing_docs = load_existing_file_docs(self.paths.file_doc_dir, group_files)
            doc_by_id = {str(doc.get("id") or ""): doc for doc in existing_docs}
            filtered_groups = {
                basename: [file_id for file_id in ids if file_id in doc_by_id]
                for basename, ids in same_name_groups.items()
            }
            filtered_groups = {basename: ids for basename, ids in filtered_groups.items() if len(ids) > 1}
            update_groups = {
                basename: ids
                for basename, ids in filtered_groups.items()
                if any(
                    file_id in module_target_ids
                    and not str(doc_by_id[file_id].get("same_name_distinction") or "").strip()
                    for file_id in ids
                )
            }
            if not update_groups:
                continue

            _log(
                progress_label,
                f"module {module_index}/{module_count} "
                f"Same-Name Differentiation {module.id}: {len(update_groups)} groups",
            )
            target_groups = {
                basename: [
                    file_id
                    for file_id in ids
                    if file_id in module_target_ids
                    and not str(doc_by_id[file_id].get("same_name_distinction") or "").strip()
                ]
                for basename, ids in update_groups.items()
            }
            target_groups = {basename: ids for basename, ids in target_groups.items() if ids}
            batches = _same_name_jobs(
                target_groups,
                group_batch_size=max(1, self.options.same_name_group_batch_size),
                file_batch_size=max(1, self.options.same_name_file_batch_size),
            )
            file_by_id = {item.id: item for item in group_files}
            for batch_index, group_batch in enumerate(batches, start=1):
                batch_groups = dict(group_batch)
                batch_ids = [file_id for ids in batch_groups.values() for file_id in ids]
                context_ids = _unique(
                    file_id
                    for basename in batch_groups
                    for file_id in filtered_groups.get(basename, [])
                )
                context_files = [file_by_id[file_id] for file_id in context_ids if file_id in file_by_id]
                _log(
                    progress_label,
                    f"module {module_index}/{module_count} "
                    f"Same-Name Differentiation {module.id}: "
                    f"batch {batch_index}/{len(batches)} ({len(batch_groups)} groups, {len(batch_ids)} target files)",
                )
                payload = {
                    "commit": commit,
                    "module_id": module.id,
                    "target_files": batch_ids,
                    "same_name_groups": batch_groups,
                    "same_name_group_all_paths": {
                        basename: filtered_groups[basename]
                        for basename in batch_groups
                    },
                    "file_docs": [doc_by_id[item.id] for item in context_files],
                    "file_code": [
                        {
                            "path": item.id,
                            "code": read_code(item.abs_path, max_chars=self.options.max_code_chars),
                        }
                        for item in context_files
                    ],
                }
                updates = self.generator.generate_same_name_differentiation(
                    payload,
                    file_ids=batch_ids,
                )
                update_map = {item["id"]: item["same_name_distinction"] for item in updates}
                for file_id in batch_ids:
                    path = file_doc_path(self.paths.file_doc_dir, file_id)
                    doc = load_json_if_exists(path)
                    if not doc:
                        continue
                    doc["same_name_distinction"] = update_map.get(file_id, "")
                    write_json(path, doc)

    def generate_module_docs(
        self,
        *,
        tree: GCCTree,
        commit: str,
        progress_label: str = "",
        refresh_stale: bool = False,
    ) -> None:
        modules = list(tree.modules.values())
        module_count = len(modules)
        for module_index, module in enumerate(modules, start=1):
            path = module_doc_path(self.paths.module_doc_dir, module.id)
            if path.exists() and not (refresh_stale and module_doc_is_stale(path, self.paths.file_doc_dir, module.files)):
                continue
            module_file_paths = [item.id for item in module.files]
            if not module_file_paths:
                continue
            _log(
                progress_label,
                f"module {module_index}/{module_count} "
                f"module doc {module.id} ({len(module_file_paths)} files)",
            )
            payload = {
                "commit": commit,
                "target_module": module.id,
                "module_file_paths": module_file_paths,
                "file_docs": load_existing_file_docs(self.paths.file_doc_dir, module.files),
            }
            docs = self.generator.generate_module_docs(payload, module_ids=[module.id])
            for doc in docs:
                write_json(module_doc_path(self.paths.module_doc_dir, doc["id"]), doc)


def load_existing_file_docs(file_doc_dir: Path, files: Iterable[GCCFile]) -> List[Dict[str, Any]]:
    docs: List[Dict[str, Any]] = []
    for item in files:
        doc = load_json_if_exists(file_doc_path(file_doc_dir, item.id))
        if doc:
            docs.append(doc)
    return docs


def build_tree_from_file_docs(file_doc_dir: Path) -> GCCTree:
    tree = GCCTree()
    for path in sorted(file_doc_dir.rglob("*.json")):
        doc = load_json_if_exists(path)
        file_id = str((doc or {}).get("id") or "").strip()
        if not file_id or not _is_localizable_gcc_file(file_id):
            continue
        module_id = _module_id_for_file(file_id)
        if not module_id:
            continue
        module = tree.modules.setdefault(module_id, GCCModule(id=module_id, abs_path=Path()))
        module.files.append(
            GCCFile(
                id=file_id,
                abs_path=Path(),
                module_id=module_id,
                basename=Path(file_id).name,
            )
        )
    return tree


def module_doc_is_stale(module_doc: Path, file_doc_dir: Path, files: Iterable[GCCFile]) -> bool:
    if not module_doc.exists():
        return True
    module_mtime = module_doc.stat().st_mtime
    for item in files:
        path = file_doc_path(file_doc_dir, item.id)
        if path.exists() and path.stat().st_mtime > module_mtime:
            return True
    return False


def _same_name_files(files: Iterable[GCCFile]) -> Dict[str, List[str]]:
    groups: Dict[str, List[str]] = {}
    for item in files:
        groups.setdefault(Path(item.basename).stem, []).append(item.id)
    return {name: ids for name, ids in groups.items() if len(ids) > 1}


def _same_name_jobs(
    groups: Dict[str, List[str]],
    *,
    group_batch_size: int,
    file_batch_size: int,
) -> List[List[tuple[str, List[str]]]]:
    jobs: List[List[tuple[str, List[str]]]] = []
    small_groups: List[tuple[str, List[str]]] = []
    for basename, ids in groups.items():
        if len(ids) > file_batch_size:
            for chunk in _chunks(ids, file_batch_size):
                jobs.append([(basename, chunk)])
            continue
        small_groups.append((basename, ids))

    current: List[tuple[str, List[str]]] = []
    current_file_count = 0
    for item in small_groups:
        item_file_count = len(item[1])
        would_exceed_groups = len(current) >= group_batch_size
        would_exceed_files = current and current_file_count + item_file_count > file_batch_size
        if would_exceed_groups or would_exceed_files:
            jobs.append(current)
            current = []
            current_file_count = 0
        current.append(item)
        current_file_count += item_file_count
    if current:
        jobs.append(current)
    return jobs


def _unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _chunks(items: List[T], size: int) -> Iterable[List[T]]:
    for index in range(0, len(items), size):
        yield items[index:index + size]


def _is_localizable_gcc_file(file_id: str) -> bool:
    parts = file_id.split("/")
    return len(parts) >= 2 and parts[0] == "gcc" and parts[1] != "testsuite"


def _module_id_for_file(file_id: str) -> str:
    parts = file_id.split("/")
    if len(parts) == 2 and parts[0] == "gcc":
        return root_module_id(parts[1])
    if len(parts) >= 3 and parts[0] == "gcc" and parts[1] in SPLIT_TOP_LEVEL_MODULES:
        return f"gcc/{parts[1]}/root" if len(parts) == 3 else f"gcc/{parts[1]}/{parts[2]}"
    if len(parts) >= 3 and parts[0] == "gcc" and parts[1]:
        return f"gcc/{parts[1]}"
    return ""


def _log(progress_label: str, message: str) -> None:
    prefix = f"{progress_label} " if progress_label else ""
    print(f"{_timestamp()} [docgen] {prefix}{message}", flush=True)


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
