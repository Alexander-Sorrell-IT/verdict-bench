"""The dev/test split is deterministic, and the frozen file is the split.

The split exists so E.1 can freeze it and abort on change. That only means
something if (a) re-running reproduces it byte-for-byte, and (b) the rule in
the file is the rule that produced it - both asserted here, the second by
recomputing the selection independently of runner/split.py's code path.
"""

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))

from split import build_split, split_text  # noqa: E402
from world.build_world import generate_tasks  # noqa: E402

SPEC_SPLIT = REPO_ROOT / "spec" / "split.json"
DEV_PER_FAMILY = 3


def test_rebuilding_the_split_is_byte_identical():
    first = build_split()
    second = build_split()
    assert split_text(first) == split_text(second)


def test_dev_and_test_are_disjoint_and_cover_every_task():
    split = build_split()
    tasks = generate_tasks()
    every = {t["task_id"] for t in tasks}
    dev = {e["task_id"] for e in split["dev"]}
    test = {e["task_id"] for e in split["test"]}
    assert not (dev & test)
    assert dev | test == every


def test_three_dev_two_test_per_family():
    split = build_split()
    from collections import Counter

    dev = Counter(e["domain"] for e in split["dev"])
    test = Counter(e["domain"] for e in split["test"])
    assert set(dev.values()) == {DEV_PER_FAMILY}
    assert set(test.values()) == {2}


def test_the_selection_follows_the_stated_rule():
    """Independent recomputation: per family, the dev set is the
    DEV_PER_FAMILY smallest sha256(task_id) values."""
    tasks = generate_tasks()
    by_family: dict = {}
    for task in tasks:
        by_family.setdefault(task["family"], []).append(task["task_id"])

    expected_dev, expected_test = set(), set()
    for _family, ids in by_family.items():
        ordered = sorted(ids, key=lambda i: hashlib.sha256(i.encode()).hexdigest())
        expected_dev |= set(ordered[:DEV_PER_FAMILY])
        expected_test |= set(ordered[DEV_PER_FAMILY:])

    split = build_split()
    assert {e["task_id"] for e in split["dev"]} == expected_dev
    assert {e["task_id"] for e in split["test"]} == expected_test


def test_the_frozen_file_is_the_split():
    """E.1's freeze is only meaningful if the committed file is what the rule
    produces - a hand-edited split.json fails here."""
    assert SPEC_SPLIT.is_file(), "spec/split.json missing - run python -m runner.split"
    on_disk = json.loads(SPEC_SPLIT.read_text())
    assert on_disk == build_split()


def test_the_rule_is_stated_in_the_file():
    split = json.loads(SPEC_SPLIT.read_text())
    assert "sha256(task_id)" in split["rule"]
    assert str(DEV_PER_FAMILY) in split["rule"]
