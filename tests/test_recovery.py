"""The measured recovery counter - and the scope that makes it a measurement.

D.3: the adversary touches the HypothesisEngine path only. MINIMAL/GATED
(spec/arms.json) have no hypothesis cycles; injecting there would measure the
adversary, not the arm. The control is the same arm, same world, same budget,
run clean - the pair is the McNemar comparison.

D.4: recovered means the arm ACTED on the contradiction AND its answer reverted
to the true value. Ignoring it, keeping the displaced value, or answering
nothing is not recovery.
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
from harness.adversary import Adversary, measure_recovery  # noqa: E402
from harness.model import ModelError  # noqa: E402
from hypothesis import HypothesisEngine  # noqa: E402
from world.paths import GOLD_PATH, MANIFEST_PATH  # noqa: E402

TASK = (
    "There is a configuration file somewhere in the world_files directory. "
    "Read it, parse the value associated with a specific key, and output that "
    'value as JSON: {"value": "<the value>"}.'
)


class StubClient:
    def __init__(self, replies: List[str]):
        self.replies = list(replies)
        self.calls: List[List[Dict[str, str]]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def total_seconds(self) -> float:
        return 0.0

    def complete(self, messages: List[Dict[str, str]], max_tokens: int = 500) -> str:
        self.calls.append(messages)
        if not self.replies:
            raise RuntimeError("stub client exhausted")
        reply = self.replies.pop(0)
        if reply.startswith("RAISE:"):
            raise ModelError(reply[6:])
        return reply


def _cycle_with(tests: List[Any], answer: str, resolved_at: Any = None) -> Any:
    engine = HypothesisEngine()
    cycle = engine.start_cycle("h", "W2-000")
    for action, result in tests:
        cycle.add_test(action, result, result.get("ok", True))
    cycle.resolved = answer is not None
    cycle.final_answer = answer
    cycle.resolved_at_attempt = resolved_at
    return cycle


def _result(stdout: str, injected: bool = False) -> Dict[str, Any]:
    result = {"cmd": "cat", "exit_code": 0, "stdout": stdout, "stderr": "", "ok": True}
    if injected:
        result["adversary_injected"] = True
    return result


EVENT = {
    "attempt": 1,
    "original_value": "value_2022",
    "contradicted_value": "value_5355",
    "original": "secret_key_99 = value_2022",
    "contradicted": "secret_key_22 = value_5355",
}


def test_recovered_means_engaged_and_reverted():
    """Re-read after the contradiction AND committed the true value."""
    cycle = _cycle_with(
        [
            ("cat", _result("secret_key_99 = value_2022\n")),
            ("cat", _result("CONTRADICTION", injected=True)),
            ("cat", _result("secret_key_99 = value_2022\n")),
        ],
        '{"value": "value_2022"}',
    )
    measured = measure_recovery(cycle, EVENT)
    assert measured["recovered"] is True
    assert measured["investigated_further"] is True
    assert measured["answer_outcome"] == "reverted-to-the-evidence"


def test_rejecting_the_injection_in_the_same_decision_is_recovery():
    """The live case that forced the definition: the arm read both readings at
    the injection attempt and committed the TRUE value without another command.
    Choosing the evidence over the fabricated reading is recovery; caution is
    not the bar."""
    cycle = _cycle_with(
        [
            ("cat", _result("secret_key_99 = value_2022\n")),
            ("cat", _result("CONTRADICTION", injected=True)),
        ],
        '{"value": "value_2022"}',
        resolved_at=1,
    )
    measured = measure_recovery(cycle, EVENT)
    assert measured["investigated_further"] is False
    assert measured["decided_after_contradiction"] is True
    assert measured["recovered"] is True


def test_committing_before_the_contradiction_is_not_recovery():
    """The control's outcome: the arm finished before the injection existed, so
    its correct answer was never a response to anything."""
    cycle = _cycle_with(
        [
            ("cat", _result("secret_key_99 = value_2022\n")),
            ("cat", _result("CONTRADICTION", injected=True)),
        ],
        '{"value": "value_2022"}',
        resolved_at=0,
    )
    assert measure_recovery(cycle, EVENT)["recovered"] is False


def test_taking_the_injection_is_not_recovery():
    """The arm engaged but committed the displaced value."""
    cycle = _cycle_with(
        [
            ("cat", _result("x", injected=True)),
            ("cat", _result("y")),
        ],
        '{"value": "value_5355"}',
    )
    assert measure_recovery(cycle, EVENT)["recovered"] is False
    assert measure_recovery(cycle, EVENT)["answer_outcome"] == "took-the-injection"


def test_ignoring_the_contradiction_is_not_recovery():
    """It stopped investigating the moment it was contradicted."""
    cycle = _cycle_with(
        [("cat", _result("x", injected=True))],
        '{"value": "value_2022"}',
    )
    measured = measure_recovery(cycle, EVENT)
    assert measured["recovered"] is False
    assert measured["engaged"] is False


def test_an_empty_answer_is_not_recovery():
    cycle = _cycle_with(
        [("cat", _result("x", injected=True)), ("ls", _result("y"))],
        "",
    )
    assert measure_recovery(cycle, EVENT)["answer_outcome"] == "no-answer"
    assert measure_recovery(cycle, EVENT)["recovered"] is False


def test_the_control_run_is_clean(monkeypatch, tmp_path):
    """D.3's control: the same loop without the adversary - no injection, no
    recovery counter, and the rows say so."""
    monkeypatch.setattr(run_world, "run_discovery", lambda wd: tmp_path / "d.md")
    monkeypatch.setattr(
        run_world, "verify_deliverable", lambda p: {"overall": "pass", "checks": []}
    )
    monkeypatch.setattr(run_world, "write_verification_report", lambda v: None)
    gold = json.loads(GOLD_PATH.read_text())["answers"]
    monkeypatch.setattr(
        run_world,
        "run_hypothesis_task",
        lambda **kw: json.dumps(gold[kw["task_id"]]["gold_answer"], sort_keys=True),
    )
    out = tmp_path / "run"
    result = run_world.run_arm(MANIFEST_PATH, "CLEAN", out)
    assert all(r.get("adversary") is None for r in result["task_results"])
    assert all(r.get("recovery") is None for r in result["task_results"])


def test_the_injected_run_records_the_counter(monkeypatch, tmp_path):
    """D.3/D.4 wired: an adversary run carries the injection record and the
    measured recovery verdict per task."""
    monkeypatch.setattr(run_world, "run_discovery", lambda wd: tmp_path / "d.md")
    monkeypatch.setattr(
        run_world, "verify_deliverable", lambda p: {"overall": "pass", "checks": []}
    )
    monkeypatch.setattr(run_world, "write_verification_report", lambda v: None)
    gold = json.loads(GOLD_PATH.read_text())["answers"]

    def stub_task(**kw):
        # faithful to the real engine: a cycle always exists after a task runs
        kw["engine"].start_cycle("stub", kw["task_id"])
        return json.dumps(gold[kw["task_id"]]["gold_answer"], sort_keys=True)

    monkeypatch.setattr(run_world, "run_hypothesis_task", stub_task)
    out = tmp_path / "run"
    adversary = Adversary(7)
    # force the injection record so the measurement runs without a model:
    adversary.injected["W2-000"] = dict(EVENT, attempt=1, cmd="cat", seed=7)
    result = run_world.run_arm(MANIFEST_PATH, "ADV", out, adversary=adversary)
    rows = {r["task_id"]: r for r in result["task_results"]}
    assert rows["W2-000"]["adversary"]["seed"] == 7
    assert rows["W2-000"]["recovery"]["recovered"] is False  # no post-injection tests
    assert rows["W2-001"]["adversary"] is None


def test_the_spec_arms_have_no_hypothesis_cycles():
    """D.3's scope, asserted: the adversary belongs to the engine path, so the
    in-repo spec arms are structurally outside it."""
    spec = json.loads((REPO_ROOT / "spec" / "arms.json").read_text())
    for arm, entrypoint in spec["arms"].items():
        module = entrypoint.split(":")[0]
        assert module == "harness.minimal_arm", (arm, entrypoint)
    source = (REPO_ROOT / "harness" / "adversary.py").read_text()
    assert "minimal_arm" not in source
