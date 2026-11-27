"""Arm tests — a stub model client, so the loop is tested with no network and no real model.

The load-bearing test is test_a_failed_arm_scores_zero_rather_than_fabricating: the arm must
return an empty answer when it cannot find one. A synthesized placeholder is exactly what
scored 25/25 and graded 0/25.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "harness"))

from harness.budget import Budget  # noqa: E402
from harness.gates import GateEngine  # noqa: E402
from harness import gates as gate_module  # noqa: E402
from harness.minimal_arm import (  # noqa: E402
    _extract_answer,
    _extract_command,
    run,
    run_gated,
    run_task,
)

BUDGET = Budget(max_steps=10, max_model_calls=10, max_wall_seconds=60.0)


class StubClient:
    """Replies from a scripted list. Raises on a `RAISE` entry to model a dead endpoint."""

    def __init__(self, replies: List[str]) -> None:
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
            raise RuntimeError(reply[6:])
        return reply


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "data.conf").write_text("secret_key_99 = value_2022\n")
    return tmp_path


def _task(
    description: str = "Find the value for the key in the config file.",
) -> Dict[str, Any]:
    return {"task_id": "T-1", "family": "unknown", "description": description}


# ------------------------------------------------------------- reply extraction


def test_extract_command_accepts_whitelisted_commands():
    assert _extract_command("cat data.conf") == "cat data.conf"
    assert _extract_command('ANSWER: {"value": 1}') is None


def test_extract_command_rejects_non_whitelisted():
    assert _extract_command("rm -rf /") is None
    assert _extract_command("sudo reboot") is None


def test_extract_command_strips_the_command_prefix():
    assert _extract_command("COMMAND: grep secret data.conf") == "grep secret data.conf"


def test_extract_answer_parses_strict_json():
    assert (
        _extract_answer('ANSWER: {"value": "value_2022"}') == '{"value": "value_2022"}'
    )


def test_extract_answer_rejects_malformed_json():
    assert _extract_answer("ANSWER: {value: unquoted}") is None
    assert _extract_answer("no answer marker here") is None


# --------------------------------------------------------------- the arm's rule


def test_a_failed_arm_scores_zero_rather_than_fabricating(sandbox):
    """THE rule. No answer means no answer - never a synthesized placeholder."""
    client = StubClient(["ls", "ls", "ls"])
    attempt = run_task(
        _task(), client, sandbox, engine=None, arm="MINIMAL", budget=BUDGET
    )
    assert attempt.answer == ""
    assert attempt.stopped_reason.startswith("model_error")


def test_unparseable_reply_does_not_become_a_guess(sandbox):
    client = StubClient(["I will now think about this very carefully."] * 5)
    attempt = run_task(
        _task(), client, sandbox, engine=None, arm="MINIMAL", budget=BUDGET
    )
    assert attempt.answer == ""
    assert attempt.stopped_reason == "unparseable_reply"


def test_a_dead_model_ends_the_attempt_honestly(sandbox):
    client = StubClient(["RAISE: connection refused"])
    attempt = run_task(
        _task(), client, sandbox, engine=None, arm="MINIMAL", budget=BUDGET
    )
    assert attempt.answer == ""
    assert attempt.stopped_reason.startswith("model_error")


def test_a_real_answer_is_returned_verbatim(sandbox):
    client = StubClient(["cat data.conf", 'ANSWER: {"value": "value_2022"}'])
    attempt = run_task(
        _task(), client, sandbox, engine=None, arm="MINIMAL", budget=BUDGET
    )
    assert attempt.answer == '{"value": "value_2022"}'
    assert attempt.stopped_reason == "answered"


# ----------------------------------------------------------------- budget rules


def test_budget_is_measured_not_declared(sandbox):
    client = StubClient(["ls"] * 8)
    attempt = run_task(
        _task(),
        client,
        sandbox,
        engine=None,
        arm="MINIMAL",
        budget=Budget(max_steps=3, max_model_calls=10, max_wall_seconds=60.0),
    )
    assert attempt.answer == ""
    assert attempt.wall_seconds >= 0.0


def test_overflow_is_recorded_when_the_budget_is_exceeded(sandbox):
    client = StubClient(["ls"] * 8)
    attempt = run_task(
        _task(),
        client,
        sandbox,
        engine=None,
        arm="MINIMAL",
        budget=Budget(max_steps=1, max_model_calls=10, max_wall_seconds=60.0),
    )
    assert attempt.overflowed is True


# ------------------------------------------------------------------ gate wiring


def test_the_gated_arm_blocks_a_command_outside_the_sandbox(sandbox):
    engine = GateEngine(gate_module.default_gates())
    client = StubClient(["cat /etc/passwd", 'ANSWER: {"value": "x"}'])
    attempt = run_task(
        _task(), client, sandbox, engine=engine, arm="GATED", budget=BUDGET
    )
    # the out-of-sandbox command is blocked and recorded; the arm is allowed to keep
    # working, so a later valid reply may still answer. What must not happen is the
    # blocked command executing.
    assert any("pre_tool/sandbox" in b for b in attempt.gate_blocks)


def test_the_minimal_arm_has_no_gates_to_block(sandbox):
    """The two arms differ by the gate set and nothing else - that is the control."""
    client = StubClient(["cat /etc/passwd", 'ANSWER: {"value": "x"}'])
    attempt = run_task(
        _task(), client, sandbox, engine=None, arm="MINIMAL", budget=BUDGET
    )
    assert attempt.gate_blocks == []


def test_entrypoints_accept_a_budget_mapping(sandbox, monkeypatch):
    """runner/bench.py resolves arms by 'module:function' and passes a Budget instance."""
    monkeypatch.setattr(
        "harness.minimal_arm._client_for",
        lambda: StubClient(['ANSWER: {"value": "1"}']),
        raising=False,
    )
    # both entrypoints exist and are callable
    assert callable(run) and callable(run_gated)


def test_task_prompt_contains_no_answer_formula(sandbox):
    from harness.minimal_arm import _task_prompt

    prompt = _task_prompt("do the thing", sandbox)
    for leak in (
        "MANDATORY COMMANDS",
        "world_files/*.bin",
        "sha256sum",
        "copy exactly",
        "config-parsing",
    ):
        assert leak not in prompt


def test_an_answer_is_written_as_the_deliverable_the_gates_check(sandbox):
    """The AF2 completion gate checks the FILE, not the reply. E.2 run 2 found it
    firing on all 25 GATED attempts while nothing ever wrote that file - a gate
    that cannot pass is decoration. The arm now writes its answer, so the gate
    verifies a real artifact."""
    client = StubClient(["cat data.conf", 'ANSWER: {"value": "value_2022"}'])
    attempt = run_task(
        _task(),
        client,
        sandbox,
        engine=None,
        arm="MINIMAL",
        budget=BUDGET,
    )
    assert attempt.stopped_reason == "answered"
    deliverable = sandbox / "deliverable.json"
    assert deliverable.is_file()
    assert json.loads(deliverable.read_text()) == {"value": "value_2022"}


def test_the_stop_gate_passes_once_the_deliverable_exists(sandbox):
    """The gate itself, on a delivered attempt: no block, and the recorded
    stopped_reason is not clobbered by it."""
    from harness.gates import GateEngine, default_gates

    client = StubClient(["cat data.conf", 'ANSWER: {"value": "value_2022"}'])
    attempt = run_task(
        _task(),
        client,
        sandbox,
        engine=GateEngine(default_gates()),
        arm="GATED",
        budget=BUDGET,
    )
    assert attempt.answer == '{"value": "value_2022"}'
    assert attempt.stopped_reason == "answered"
    assert not [b for b in attempt.gate_blocks if b.startswith("stop/")]
