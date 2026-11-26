"""Independent solver — a world ships only if every task is solvable in code.

The committed world must reproduce 100% from public files. If someone later
ships a task whose answer no public file can produce, this file fails - that is
the alignerr-task-factory rule ("refuses to ship paradoxes") made executable.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world import independent_solver as solver  # noqa: E402
from world.paths import FILES_DIR, GOLD_PATH, MANIFEST_PATH  # noqa: E402


def test_the_committed_world_reproduces_every_gold_hash():
    result = solver.solve_world()
    assert result["total"] == 25
    assert result["reproduced"] == result["total"], [
        d for d in result["details"] if not d["reproduced"]
    ]


def test_solver_main_exits_zero_on_the_committed_world():
    proc = subprocess.run(
        [sys.executable, "-m", "world.independent_solver"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_tampered_gold_hash_fails_loudly(tmp_path):
    """A flipped byte in the answer key must show up as a mismatch, not a pass."""
    gold = json.loads(GOLD_PATH.read_text())
    task_id = sorted(gold["answers"])[0]
    gold["answers"][task_id]["gold_hash"] = "0" * 64
    tampered = tmp_path / "gold.json"
    tampered.write_text(json.dumps(gold))

    result = solver.solve_world(gold_path=tampered)
    assert result["reproduced"] == result["total"] - 1
    bad = next(d for d in result["details"] if not d["reproduced"])
    assert bad["task_id"] == task_id
    assert bad["reason"] == "hash mismatch"


def test_missing_evidence_is_reported_not_skipped(tmp_path):
    """A task with no public evidence file is a paradox: recorded, not ignored."""
    empty = tmp_path / "files"
    empty.mkdir()
    result = solver.solve_world(files_dir=empty)
    assert result["reproduced"] == 0
    assert all(not d["reproduced"] for d in result["details"])
    assert "evidence file" in result["details"][0]["reason"]


def test_the_solver_does_not_read_gold_to_choose_an_answer(tmp_path):
    """The anti-lie guard, made discriminating: plant a fake answer in gold AND
    its matching hash. A solver that picks its answer from gold returns the plant
    and passes; the honest one returns the file's value and must fail this test's
    way - the file wins, the plant loses."""
    import hashlib

    gold = json.loads(GOLD_PATH.read_text())
    task_id = sorted(gold["answers"])[0]
    original_answer = dict(gold["answers"][task_id]["gold_answer"])
    planted = {"value": "planted_by_a_cheat"}
    gold["answers"][task_id]["gold_answer"] = planted
    gold["answers"][task_id]["gold_hash"] = hashlib.sha256(
        json.dumps(planted, sort_keys=True).encode()
    ).hexdigest()
    tampered = tmp_path / "gold.json"
    tampered.write_text(json.dumps(gold))

    result = solver.solve_world(gold_path=tampered)
    record = next(d for d in result["details"] if d["task_id"] == task_id)
    assert record["answer"] == original_answer
    assert not record["reproduced"]


def test_a_world_file_is_named_for_its_task():
    """Public convention the solver relies on: <task_id>.<ext> in world_files."""
    manifest = json.loads(MANIFEST_PATH.read_text())
    for task in manifest["tasks"]:
        matches = sorted(FILES_DIR.glob(f"{task['task_id']}.*"))
        assert len(matches) == 1, f"{task['task_id']}: {[p.name for p in matches]}"
