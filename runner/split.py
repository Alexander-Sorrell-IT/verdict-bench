#!/usr/bin/env python3
# verdict-bench runner/split.py
#
# The dev/test split, in the shape GitHub's apex/SPLIT.json uses (B.1): the rule
# is stated IN the file, and re-running it reproduces the same sets. Nothing is
# sampled and nothing is timestamped - "frozen before any arm runs" (E.1) has
# to mean reproducible, not merely recorded.
#
# Rule (ported from apex): per domain, tasks sorted by sha256(task_id); the
# first DEV_PER_FAMILY are dev, the rest are test. World 2 has five tasks per
# family, so apex's "first 3 = dev, next 7 = test" lands as 3 dev / 2 test.
#
# The split is built from the generator's private task list. Families are
# withheld from the agent (A.1), so the split - which names them - is withheld
# too: it lives in spec/, never in the agent-visible tree.

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))

from world.build_world import generate_tasks  # noqa: E402

DEFAULT_SEED = 20261120
DEFAULT_NUM_TASKS = 25
DEV_PER_FAMILY = 3

RULE = (
    "per family, tasks sorted by sha256(task_id); "
    f"first {DEV_PER_FAMILY} = dev, remainder = test"
)


def _hash_key(task_id: str) -> str:
    return hashlib.sha256(task_id.encode()).hexdigest()


def build_split(
    seed: int = DEFAULT_SEED, num_tasks: int = DEFAULT_NUM_TASKS
) -> Dict[str, Any]:
    """Build the split. Deterministic for (seed, num_tasks): no sampling, no clock."""
    tasks = generate_tasks(seed=seed, num_tasks=num_tasks)

    by_family: Dict[str, List[Dict[str, str]]] = {}
    for task in tasks:
        by_family.setdefault(task["family"], []).append(
            {"task_id": task["task_id"], "domain": task["family"]}
        )

    dev: List[Dict[str, str]] = []
    test: List[Dict[str, str]] = []
    for family in sorted(by_family):
        entries = sorted(by_family[family], key=lambda e: _hash_key(e["task_id"]))
        dev.extend(entries[:DEV_PER_FAMILY])
        test.extend(entries[DEV_PER_FAMILY:])

    return {
        "rule": RULE,
        "world_id": f"world2-{seed}",
        "seed": seed,
        "num_tasks": num_tasks,
        # Selection is by sha256(task_id); the lists are written in task_id order
        # so a human reviewer can read them. Both orders are reproducible.
        "dev": sorted(dev, key=lambda e: e["task_id"]),
        "test": sorted(test, key=lambda e: e["task_id"]),
    }


def split_text(split: Dict[str, Any]) -> str:
    """The canonical serialization: no timestamps, so re-runs are byte-identical."""
    return json.dumps(split, indent=2, sort_keys=False) + "\n"


def write_split(path: Path, split: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(split_text(split))


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else [])
    out = Path(argv[0]) if argv else BENCH_DIR / "spec" / "split.json"
    split = build_split()
    write_split(out, split)
    print(f"{out}: {len(split['dev'])} dev / {len(split['test'])} test")
    print(f"  rule: {RULE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
