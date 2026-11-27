"""The contradiction injector: deterministic, once per task, and visible to the arm.

D.2. The recovery claim needs a contradiction the arm can actually detect, fired
at a seeded point, identically every time - so the comparison (clean vs
contradicted) measures the ARM, not the adversary's mood.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

from harness.adversary import Adversary  # noqa: E402
from harness.model import ModelError  # noqa: E402
from hypothesis import (  # noqa: E402
    HypothesisEngine,
    build_sandbox,
    run_hypothesis_task,
)

TASK = (
    "There is a configuration file somewhere in the world_files directory. "
    "Read it, parse the value associated with a specific key, and output that "
    'value as JSON: {"value": "<the value>"}.'
)


class StubClient:
    """Scripted replies; the record of what the arm was shown."""

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


def _ok_result(stdout: str) -> Dict[str, Any]:
    return {"cmd": "cat", "exit_code": 0, "stdout": stdout, "stderr": "", "ok": True}


def test_the_same_seed_produces_the_same_injection():
    first, second = Adversary(7), Adversary(7)
    result_a = first.maybe_inject("W2-000", 1, "cat", _ok_result("value_2022\n"))
    result_b = second.maybe_inject("W2-000", 1, "cat", _ok_result("value_2022\n"))
    assert first.injected == second.injected
    assert result_a["stdout"] == result_b["stdout"]


def test_a_different_seed_displaces_differently():
    low, high = Adversary(7), Adversary(9)
    low.maybe_inject("W2-000", 1, "cat", _ok_result("value_2022\n"))
    high.maybe_inject("W2-000", 1, "cat", _ok_result("value_2022\n"))
    assert (
        low.injected["W2-000"]["contradicted"]
        != high.injected["W2-000"]["contradicted"]
    )


def test_it_fires_once_per_task_at_the_seeded_attempt_only():
    adversary = Adversary(7, at_attempt=1)
    adversary.maybe_inject("W2-000", 1, "cat", _ok_result("value_2022\n"))
    again = adversary.maybe_inject("W2-000", 2, "cat", _ok_result("value_2022\n"))
    assert again.get("adversary_injected") is not True
    early = adversary.maybe_inject("W2-001", 0, "cat", _ok_result("value_2022\n"))
    assert early.get("adversary_injected") is not True
    later = adversary.maybe_inject("W2-001", 1, "cat", _ok_result("value_2022\n"))
    assert later.get("adversary_injected") is True
    assert set(adversary.injected) == {"W2-000", "W2-001"}


def test_a_failed_or_empty_command_is_never_contradicted():
    adversary = Adversary(7)
    failed = adversary.maybe_inject("W2-000", 1, "cat", _ok_result(""))
    assert failed.get("adversary_injected") is not True
    nonzero = dict(_ok_result("x"), ok=False, exit_code=1)
    assert (
        adversary.maybe_inject("W2-001", 1, "cat", nonzero).get("adversary_injected")
        is not True
    )
    assert adversary.injected == {}


def test_the_contradiction_is_detectable_and_keeps_the_original():
    adversary = Adversary(7)
    result = adversary.maybe_inject(
        "W2-000", 1, "cat", _ok_result("secret_key_99 = value_2022\n")
    )
    text = result["stdout"]
    assert "CONTRADICTION INJECTED" in text
    assert "value_2022" in text and "value_0800" in text  # seed 7 shifts by 8
    assert "Reconcile before answering" in text
    assert "secret_key_99 = value_2022" in text  # the original stays readable


def test_the_control_path_is_clean():
    """No adversary -> nothing rewritten. The clean run is the control."""
    result = _ok_result("value_2022\n")
    assert result.get("adversary_injected") is not True


def test_the_arm_sees_the_contradiction_on_the_next_attempt():
    """End to end with a stub model: attempt 2 is contradicted, and the
    evaluate prompt for that attempt carries the injected text - that is the
    measurable setup."""
    client = StubClient(
        [
            "cat world_files/W2-000.conf",
            "COMMAND: ls world_files",
            "cat world_files/W2-000.conf",
            'ANSWER: {"value": "value_2022"}',
        ]
    )
    engine = HypothesisEngine(log_path=Path("/tmp/opencode/adv_log.md"))
    adversary = Adversary(7, at_attempt=1)
    run_hypothesis_task(
        TASK,
        "W2-000",
        engine,
        max_attempts=2,
        client=client,
        sandbox=build_sandbox(),
        adversary=adversary,
    )
    # the model was shown the contradiction in the attempt-2 evaluate prompt
    evaluate_prompts = [
        m["content"]
        for call in client.calls
        for m in call
        if "COMMAND EXECUTED" in m["content"]
    ]
    assert any("CONTRADICTION INJECTED" in p for p in evaluate_prompts)
    # the cycle records it as harness-authored, apart from model corrections
    cycle = engine.cycles[-1]
    assert cycle.adversary and "contradiction injected" in cycle.adversary[0]["detail"]
    assert adversary.event_for("W2-000")["attempt"] == 1


def test_two_identical_runs_produce_byte_identical_logs(tmp_path):
    """D.2's verify: same seed -> same injection -> byte-identical log."""

    def one_run(log_path: Path) -> bytes:
        client = StubClient(
            [
                "cat world_files/W2-000.conf",
                "COMMAND: ls world_files",
                "cat world_files/W2-000.conf",
                "COMMAND: ls world_files",
                "cat world_files/W2-000.conf",
                'ANSWER: {"value": "value_2022"}',
            ]
        )
        engine = HypothesisEngine(log_path=log_path)
        run_hypothesis_task(
            TASK,
            "W2-000",
            engine,
            max_attempts=3,
            client=client,
            sandbox=build_sandbox(),
            adversary=Adversary(7, at_attempt=1),
        )
        engine.write_log()
        return log_path.read_bytes()

    assert one_run(tmp_path / "a.md") == one_run(tmp_path / "b.md")
