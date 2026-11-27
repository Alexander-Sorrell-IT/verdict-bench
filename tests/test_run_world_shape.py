"""The ported loop shape: rows stats.py can read, verdicts from gold, never from
the self-verification's file-exists check.

Dry-run offline (B.3's verify): the model-dependent pieces are stubbed, so the
loop, the split wiring, the grading and the row record all execute for real.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "world"))

import run_world  # noqa: E402
from stats import load_attempts  # noqa: E402
from world.paths import GOLD_PATH, MANIFEST_PATH  # noqa: E402


def _stub_arm(monkeypatch, tmp_path):
    """Stub the model and the CWD-writing self-verify; leave everything else real."""
    gold = json.loads(GOLD_PATH.read_text())["answers"]

    def fake_hypothesis(**kwargs):
        task_id = kwargs["task_id"]
        answer = gold[task_id]["gold_answer"]
        return json.dumps(answer, sort_keys=True)

    monkeypatch.setattr(run_world, "run_hypothesis_task", fake_hypothesis)
    monkeypatch.setattr(
        run_world, "run_discovery", lambda world_dir: tmp_path / "discovery.md"
    )
    monkeypatch.setattr(
        run_world, "verify_deliverable", lambda p: {"overall": "pass", "checks": []}
    )
    monkeypatch.setattr(run_world, "write_verification_report", lambda v: None)


def test_rows_are_stats_compatible_and_family_recorded(monkeypatch, tmp_path):
    _stub_arm(monkeypatch, tmp_path)
    out = tmp_path / "run"
    split = run_world._load_split(REPO_ROOT / "spec" / "split.json")

    result = run_world.run_arm(MANIFEST_PATH, "STUB", out, split=split)

    rows = [
        json.loads(line) for line in (out / "results.jsonl").read_text().splitlines()
    ]
    assert len(rows) == result["total_tasks"] == 25
    for row in rows:
        for field in (
            "arm",
            "task_id",
            "task_pass",
            "criteria_passed",
            "criteria_total",
        ):
            assert field in row, f"{field} missing from {row['task_id']}"
        assert row["family"] != "unknown", "the runner lost the family map"
        assert row["split"] in ("dev", "test")
    # stats.py - the World-1 lineage's reader - parses the file unchanged.
    attempts = load_attempts(out / "results.jsonl")
    assert len(attempts) == 25


def test_a_correct_answer_passes_and_a_wrong_one_fails(monkeypatch, tmp_path):
    """The verdict is the hash grade: exact gold passes, anything else fails,
    however well-formed it is."""
    _stub_arm(monkeypatch, tmp_path)
    gold = json.loads(GOLD_PATH.read_text())["answers"]
    first_task = min(gold)

    def half_right(**kwargs):
        answer = gold[kwargs["task_id"]]["gold_answer"]
        if kwargs["task_id"] == first_task:
            return json.dumps(answer, sort_keys=True)
        return json.dumps({"value": "confidently wrong"}, sort_keys=True)

    monkeypatch.setattr(run_world, "run_hypothesis_task", half_right)
    out = tmp_path / "run"
    result = run_world.run_arm(MANIFEST_PATH, "STUB", out)

    rows = {r["task_id"]: r for r in result["task_results"]}
    assert rows[first_task]["task_pass"] is True
    wrong = next(t for t in rows if t != first_task)
    assert rows[wrong]["task_pass"] is False
    assert rows[wrong]["criteria_passed"] == 0
    # Wrong but valid JSON - the file-exists/parses check would have "passed" it.
    assert rows[wrong]["valid_json"] is True
    assert rows[wrong]["self_verify"] == "pass"


def test_the_split_filter_runs_only_its_members(monkeypatch, tmp_path):
    _stub_arm(monkeypatch, tmp_path)
    out = tmp_path / "run-dev"
    split = run_world._load_split(REPO_ROOT / "spec" / "split.json")

    result = run_world.run_arm(
        MANIFEST_PATH, "STUB", out, split=split, only_split="dev"
    )
    assert result["total_tasks"] == 15
    assert all(r["split"] == "dev" for r in result["task_results"])


def test_the_self_verify_pass_is_not_the_verdict(monkeypatch, tmp_path):
    """The exact defect that manufactured 25/25, as a regression test: every row
    self-verifies "pass" (the file exists, is non-empty, parses) and the graded
    summary must still say 0/25."""
    _stub_arm(monkeypatch, tmp_path)
    monkeypatch.setattr(
        run_world,
        "run_hypothesis_task",
        lambda **kwargs: json.dumps({"value": "confidently wrong"}),
    )
    out = tmp_path / "run"
    result = run_world.run_arm(MANIFEST_PATH, "STUB", out)

    assert len(result["task_results"]) == 25
    assert all(r["self_verify"] == "pass" for r in result["task_results"])
    assert all(r["valid_json"] for r in result["task_results"])
    assert result["passed"] == 0
