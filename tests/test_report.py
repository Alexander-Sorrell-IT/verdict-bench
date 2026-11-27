"""The report is generated, not written: every number from the artifacts, and
the same artifacts regenerate the same bytes.

It also carries the World-1 finding as a regression: the recorded attempts
cannot be re-graded against any surviving gold, so the sealed grader rejects
them - the live-regression impossibility is now a computed fact, not prose.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

import run_world  # noqa: E402
from report import build_report  # noqa: E402
from world.paths import GOLD_PATH, MANIFEST_PATH  # noqa: E402


class RouteClient:
    """Carries the model route into the rows; no calls are made."""

    model = "nvidia/nemotron-3-ultra-550b-a55b"
    base_url = "https://integrate.api.nvidia.com/v1"


WORLD1_A_B = REPO_ROOT / "results" / "world1" / "world1_A_B_results.jsonl"
WORLD1_AF2 = REPO_ROOT / "results" / "world1" / "world1_AF2_results.jsonl"


def test_the_same_artifacts_regenerate_byte_identically():
    first = build_report(WORLD1_A_B, GOLD_PATH)
    second = build_report(WORLD1_A_B, GOLD_PATH)
    assert first["text"] == second["text"]


def test_the_world1_headline_is_restated_with_the_voids_applied():
    data = build_report(WORLD1_A_B, GOLD_PATH)
    assert data["voids"] == 15
    per_arm = {s["arm"]: s for s in data["summaries"]}
    # the recorded 18/25 and 17/25, after the timeout rows are voided:
    assert per_arm["A"]["tasks_passed"] == 16
    assert per_arm["A"]["tasks_scored"] == 22
    assert per_arm["B"]["tasks_passed"] == 13
    assert per_arm["B"]["tasks_scored"] == 13
    assert "voided" in data["text"]
    assert "timeout" in data["text"]


def test_the_comparison_pairs_on_the_tasks_both_arms_completed():
    data = build_report(WORLD1_A_B, GOLD_PATH)
    # the pairing, recomputed independently of the report's own code path
    from stats import index_by_task, load_attempts

    attempts = load_attempts(WORLD1_A_B)
    by_arm: Dict[str, List[Any]] = {}
    for attempt in attempts:
        by_arm.setdefault(attempt.arm, []).append(attempt)
    shared = set(index_by_task(by_arm["A"])) & set(index_by_task(by_arm["B"]))
    assert len(data["comparison"]["pairs"]) == 1
    pair = data["comparison"]["pairs"][0]
    assert pair["paired_tasks"] == len(shared)
    assert pair["discordant"] == 3
    assert pair["p_value"] == 0.25


def test_three_arms_get_all_pairwise_comparisons(tmp_path):
    """The comparison claim needs every pair, not just the first two arms."""
    rows = tmp_path / "three.jsonl"
    lines = []
    for arm in ("A", "B", "C"):
        for index, task_id in enumerate(("T1", "T2", "T3")):
            lines.append(
                json.dumps(
                    {
                        "arm": arm,
                        "task_id": task_id,
                        "task_pass": (index + len(arm)) % 2 == 0,
                        "criteria_passed": 1,
                        "criteria_total": 1,
                    }
                )
            )
    rows.write_text("\n".join(lines) + "\n")
    data = build_report(rows, GOLD_PATH)
    pairs = data["comparison"]["pairs"]
    assert [p["arms"] for p in pairs] == [["A", "B"], ["A", "C"], ["B", "C"]]
    assert all(p["paired_tasks"] == 3 for p in pairs)


def test_af2_recorded_25_25_and_survives_as_8_of_8():
    """The retracted-class number, restated honestly: 17 of its 25 rows were
    killed by the wall clock and recorded as passes."""
    data = build_report(WORLD1_AF2, GOLD_PATH)
    assert data["voids"] == 17
    summary = data["summaries"][0]
    assert summary["arm"] == "AF2"
    assert summary["tasks_passed"] == 8
    assert summary["tasks_scored"] == 8


def test_the_world1_attempts_are_rejected_because_they_cannot_be_regraded():
    """The impossibility, computed: the recorded verdicts cannot be
    independently re-graded - the gold they were graded against is gone - so
    the sealed grader refuses them. This is the finding that justifies
    offline-only scope for World 1."""
    data = build_report(WORLD1_A_B, GOLD_PATH)
    assert data["verdict"]["verdict"] == "REJECT"
    assert data["verdict"]["discrepancies"]
    assert "REJECT" in data["text"]


def test_the_report_states_what_the_gates_cannot_check():
    data = build_report(WORLD1_A_B, GOLD_PATH)
    assert "what the gates cannot check" in data["text"].lower() or (
        "gates cannot check" in data["text"]
    )


def test_perfect_agreement_is_reported_not_raised():
    """Two arms passing the same tasks is not a degenerate comparison - it is
    agreement, and p = 1.0. Raising here would fail exactly the runs that
    agree completely (the operator smoke hit this)."""
    from stats import Attempt, mcnemar

    shared = [
        Attempt("A", "T-1", "f", True, 1, 1),
        Attempt("B", "T-1", "f", True, 1, 1),
        Attempt("A", "T-2", "f", True, 1, 1),
        Attempt("B", "T-2", "f", True, 1, 1),
    ]
    a = [x for x in shared if x.arm == "A"]
    b = [x for x in shared if x.arm == "B"]
    result = mcnemar(a, b)
    assert result.discordant == 0
    assert result.p_value == 1.0


def test_a_dry_run_reports_pass_and_its_model_route(monkeypatch, tmp_path):
    """The World-2 path: stubbed arms solve everything, the rows carry the
    route, the report discloses it and the verdict is PASS."""
    gold = json.loads(GOLD_PATH.read_text())["answers"]
    monkeypatch.setattr(run_world, "run_discovery", lambda wd: tmp_path / "d.md")
    monkeypatch.setattr(
        run_world, "verify_deliverable", lambda p: {"overall": "pass", "checks": []}
    )
    monkeypatch.setattr(run_world, "write_verification_report", lambda v: None)
    monkeypatch.setattr(
        run_world,
        "run_hypothesis_task",
        lambda **kw: json.dumps(gold[kw["task_id"]]["gold_answer"], sort_keys=True),
    )
    out = tmp_path / "run"
    run_world.run_arm(MANIFEST_PATH, "STUB", out, client=RouteClient())

    data = build_report(
        out / "results.jsonl",
        GOLD_PATH,
        split_path=REPO_ROOT / "spec" / "split.json",
        receipts_path=out / "receipts.json",
        log_path=out / "hypothesis_log.md",
    )
    assert data["voids"] == 0
    assert data["summaries"][0]["tasks_passed"] == 25
    assert data["verdict"]["verdict"] == "PASS"
    assert "relay" in data["text"] or "nemotron" in data["text"]
    assert data["receipts"]["chain"]["ok"] is True
    assert data["receipts"]["log"]["ok"] is True
