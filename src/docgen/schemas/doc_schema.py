from __future__ import annotations

from typing import Any, Dict, Iterable, List


def validate_file_docs(
    raw: Any,
    *,
    target_ids: Iterable[str],
    require_empty_same_name: bool = False,
) -> List[Dict[str, str]]:
    obj = _ensure_dict(raw)
    targets = list(target_ids)
    target_set = set(targets)
    docs = obj.get("docs") if isinstance(obj.get("docs"), list) else []
    by_id: Dict[str, Dict[str, str]] = {}
    for item in docs:
        doc = _ensure_dict(item)
        doc_id = _normalize_text(doc.get("id"))
        if doc_id not in target_set:
            continue
        responsibility = _normalize_text(doc.get("responsibility"))
        if not responsibility:
            continue
        same_name_distinction = _normalize_text(doc.get("same_name_distinction"))
        if require_empty_same_name and same_name_distinction:
            raise RuntimeError(f"same_name_distinction must be empty for initial file doc: {doc_id}")
        by_id[doc_id] = {
            "id": doc_id,
            "responsibility": responsibility,
            "same_name_distinction": same_name_distinction,
        }

    missing = [doc_id for doc_id in targets if doc_id not in by_id]
    if missing:
        raise RuntimeError(f"missing file docs for: {', '.join(missing[:5])}")
    return [by_id[doc_id] for doc_id in targets]


def validate_module_docs(raw: Any, *, module_ids: Iterable[str]) -> List[Dict[str, str]]:
    obj = _ensure_dict(raw)
    targets = list(module_ids)
    target_set = set(targets)
    docs = obj.get("docs") if isinstance(obj.get("docs"), list) else []
    by_id: Dict[str, Dict[str, str]] = {}
    for item in docs:
        doc = _ensure_dict(item)
        doc_id = _normalize_text(doc.get("id"))
        if doc_id not in target_set:
            continue
        responsibility = _normalize_text(doc.get("responsibility"))
        if responsibility:
            by_id[doc_id] = {"id": doc_id, "responsibility": responsibility}

    missing = [doc_id for doc_id in targets if doc_id not in by_id]
    if missing:
        raise RuntimeError(f"missing module docs for: {', '.join(missing[:5])}")
    return [by_id[doc_id] for doc_id in targets]


def validate_same_name_updates(raw: Any, *, file_ids: Iterable[str]) -> List[Dict[str, str]]:
    obj = _ensure_dict(raw)
    targets = list(file_ids)
    target_set = set(targets)
    updates = obj.get("updates") if isinstance(obj.get("updates"), list) else []
    by_id: Dict[str, Dict[str, str]] = {}
    for item in updates:
        update = _ensure_dict(item)
        doc_id = _normalize_text(update.get("id"))
        if doc_id not in target_set:
            continue
        distinction = _normalize_text(update.get("same_name_distinction"))
        if distinction:
            by_id[doc_id] = {"id": doc_id, "same_name_distinction": distinction}

    missing = [doc_id for doc_id in targets if doc_id not in by_id]
    if missing:
        raise RuntimeError(f"missing same-name updates for: {', '.join(missing[:5])}")
    return [by_id[doc_id] for doc_id in targets]


def _ensure_dict(raw: Any) -> Dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()
