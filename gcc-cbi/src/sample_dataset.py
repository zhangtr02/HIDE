from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from fractions import Fraction
from pathlib import Path
from typing import Dict, Iterable, List

from src.common.config import load_config
from src.common.csv_io import read_dict_csv
from src.common.json_io import write_json


DEFAULT_MODULES = (
    "gcc/root",
    "gcc/cp",
    "gcc/fortran",
    "gcc/config",
    "gcc/c",
    "gcc/c-family",
)


@dataclass(frozen=True)
class ModuleQuota:
    module: str
    candidate_count: int
    quota: int
    exact_quota: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a stratified GCC bug subset by primary buggy module")
    parser.add_argument("--size", type=int, default=200, help="Target sample size")
    parser.add_argument("--seed", type=int, default=20260610, help="Random seed for reproducible sampling")
    parser.add_argument(
        "--modules",
        default=",".join(DEFAULT_MODULES),
        help="Comma-separated primary modules to sample from",
    )
    parser.add_argument("--input", default="", help="Input CSV path, default uses config paths.dataset_csv")
    parser.add_argument("--output", default="", help="Output CSV path, default gccbugs_200.csv")
    parser.add_argument("--metadata", default="", help="Output metadata JSON path, default gccbugs_200_metadata.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.size <= 0:
        raise RuntimeError("--size must be positive")

    cfg = load_config()
    root: Path = cfg["_project_root"]
    input_path = Path(args.input).resolve() if args.input else cfg["paths"]["dataset_csv"]
    output_path = Path(args.output).resolve() if args.output else root / f"gccbugs_{args.size}.csv"
    metadata_path = Path(args.metadata).resolve() if args.metadata else root / f"gccbugs_{args.size}_metadata.json"
    modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    if not modules:
        raise RuntimeError("--modules must contain at least one module")

    rows = read_dict_csv(input_path)
    fieldnames = list(rows[0].keys()) if rows else []
    if not fieldnames:
        raise RuntimeError(f"empty input CSV: {input_path}")

    strata = group_by_primary_module(rows, modules)
    candidate_count = sum(len(strata[module]) for module in modules)
    if candidate_count < args.size:
        raise RuntimeError(f"not enough candidate rows: requested {args.size}, available {candidate_count}")

    quotas = allocate_quotas({module: len(strata[module]) for module in modules}, args.size)
    rng = random.Random(args.seed)
    selected_by_module: Dict[str, List[Dict[str, str]]] = {}
    for quota in quotas:
        candidates = list(strata[quota.module])
        selected_by_module[quota.module] = rng.sample(candidates, quota.quota) if quota.quota else []

    selected_ids = {row["instance_id"] for rows_for_module in selected_by_module.values() for row in rows_for_module}
    selected_rows = [row for row in rows if row.get("instance_id") in selected_ids]
    write_csv(output_path, fieldnames, selected_rows)
    write_json(
        metadata_path,
        {
            "input_csv": str(input_path),
            "output_csv": str(output_path),
            "sample_size": len(selected_rows),
            "target_size": args.size,
            "seed": args.seed,
            "stratification": "primary_buggy_module",
            "modules": modules,
            "total_input_rows": len(rows),
            "candidate_rows_in_selected_modules": candidate_count,
            "excluded_rows_outside_selected_modules": len(rows) - candidate_count,
            "quotas": [asdict(quota) for quota in quotas],
            "selected_counts": {module: len(selected_by_module[module]) for module in modules},
            "selected_bug_ids_by_module": {
                module: [row["instance_id"] for row in selected_by_module[module]]
                for module in modules
            },
        },
    )
    print(f"[sample] wrote {output_path}")
    print(f"[sample] wrote {metadata_path}")
    for quota in quotas:
        print(f"[sample] {quota.module}: {quota.quota}/{quota.candidate_count} exact={quota.exact_quota}")


def group_by_primary_module(rows: Iterable[Dict[str, str]], modules: List[str]) -> Dict[str, List[Dict[str, str]]]:
    module_set = set(modules)
    grouped: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        primary = primary_module(row)
        if primary in module_set:
            grouped[primary].append(row)
    return {module: grouped.get(module, []) for module in modules}


def primary_module(row: Dict[str, str]) -> str:
    modules = [item.strip() for item in str(row.get("buggy_modules") or "").split(",") if item.strip()]
    return modules[0] if modules else ""


def allocate_quotas(counts: Dict[str, int], size: int) -> List[ModuleQuota]:
    total = sum(counts.values())
    if total <= 0:
        raise RuntimeError("no candidate rows in selected modules")

    exact = {module: Fraction(count, total) * size for module, count in counts.items()}
    floors = {module: int(value) for module, value in exact.items()}
    remaining = size - sum(floors.values())
    order = sorted(
        counts,
        key=lambda module: (exact[module] - floors[module], counts[module], module),
        reverse=True,
    )
    quotas = dict(floors)
    for module in order[:remaining]:
        quotas[module] += 1

    return [
        ModuleQuota(
            module=module,
            candidate_count=counts[module],
            quota=quotas[module],
            exact_quota=f"{float(exact[module]):.4f}",
        )
        for module in counts
    ]


def write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
