from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List


def read_dict_csv(path: Path) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]
