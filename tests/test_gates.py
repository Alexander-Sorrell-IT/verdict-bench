"""Gate tests — every gate must fail when its condition is absent.

A gate that passes when it should block is how a broken run gets recorded as a pass,
which is the exact failure the retracted 25/25 came from. Each test below therefore
asserts the BLOCK, not just the allow.
"""

import sys
from pathlib import Path

import hashlib
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.gates import (  # noqa: E402
    ALLOW,
    AntiHallucinationGate,
    CompletionGate,
    GateContext,
    GateEngine,
    ModelCallTimeoutGate,
    OutputValidationGate,
    ReadStreakGate,
    SandboxGate,
    Verdict,
    map_stages_to_events,
)


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "deliverable.json").write_text('{"value": "ok"}')
    return tmp_path


def _ctx(sandbox, **kw):
    defaults = dict(
        stage="pre_tool", sandbox=sandbox, deliverable=sandbox / "deliverable.json"
    )
    defaults.update(kw)
    return GateContext(**defaults)


# ---------------------------------------------------------------- stage mapping


def test_canonical_stages_map_to_platform_events():
    events = ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"]
    mapping, unmatched = map_stages_to_events(events)
    assert mapping["stop"] == "Stop"
    assert mapping["pre_tool"] == "PreToolUse"
    assert unmatched, "unmatched stages must be reported, not dropped"


def test_unmatched_stage_is_reported_not_silently_dropped():
    _mapping, unmatched = map_stages_to_events(["SessionStart"])
    assert "pre_tool" in unmatched


# ------------------------------------------------------------- completion gate


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_completion_blocks_when_deliverable_missing(sandbox):
    verdict = CompletionGate().check(
        _ctx(sandbox, stage="stop", deliverable=sandbox / "nope.json")
    )
    assert verdict.allowed is False and "does not exist" in verdict.reason


def test_completion_blocks_when_deliverable_empty(sandbox):
    empty = sandbox / "empty.json"
    empty.write_text("")
    assert (
        CompletionGate().check(_ctx(sandbox, stage="stop", deliverable=empty)).allowed
        is False
    )


def test_completion_allows_a_real_deliverable(sandbox):
    assert CompletionGate().check(_ctx(sandbox, stage="stop")).allowed is True


# -------------------------------------------------------- output validation gate


def test_output_validation_blocks_non_json(sandbox):
    bad = sandbox / "bad.json"
    bad.write_text("Task W2-000: discovered via hypothesis testing. Attempts: 2/5")
    verdict = OutputValidationGate().check(_ctx(sandbox, stage="stop", deliverable=bad))
    assert verdict.allowed is False and "not valid JSON" in verdict.reason


def test_output_validation_blocks_missing_required_keys(sandbox):
    d = sandbox / "d.json"
    d.write_text('{"other": 1}')
    verdict = OutputValidationGate().check(
        _ctx(sandbox, stage="stop", deliverable=d, required_keys=("value",))
    )
    assert verdict.allowed is False and "missing required keys" in verdict.reason


def test_output_validation_allows_required_keys_present(sandbox):
    assert (
        OutputValidationGate()
        .check(_ctx(sandbox, stage="stop", required_keys=("value",)))
        .allowed
        is True
    )


# --------------------------------------------------------------- read-streak gate


def test_read_streak_blocks_after_the_limit(sandbox):
    gate = ReadStreakGate()
    assert gate.check(_ctx(sandbox, reads_this_turn=gate.max_streak)).allowed is False


def test_read_streak_allows_below_the_limit(sandbox):
    assert ReadStreakGate().check(_ctx(sandbox, reads_this_turn=1)).allowed is True


# ------------------------------------------------------------ model-call timeout


def test_model_call_timeout_blocks_a_hung_call(sandbox):
    gate = ModelCallTimeoutGate()
    verdict = gate.check(
        _ctx(sandbox, stage="model_call", model_call_seconds=gate.max_seconds + 1)
    )
    assert verdict.allowed is False and "limit" in verdict.reason


def test_model_call_timeout_allows_within_limit(sandbox):
    assert (
        ModelCallTimeoutGate()
        .check(_ctx(sandbox, stage="model_call", model_call_seconds=1.0))
        .allowed
        is True
    )


# ---------------------------------------------------------- anti-hallucination gate


def test_anti_hallucination_blocks_an_unread_file(sandbox):
    verdict = AntiHallucinationGate().check(
        _ctx(sandbox, files_claimed_read=("never_opened.conf",))
    )
    assert verdict.allowed is False and "never read" in verdict.reason


def test_anti_hallucination_allows_a_file_that_was_read(sandbox):
    verdict = AntiHallucinationGate().check(
        _ctx(sandbox, last_files_read=("data.conf",), files_claimed_read=("data.conf",))
    )
    assert verdict.allowed is True


def test_anti_hallucination_repairs_a_stale_read(sandbox):
    target = sandbox / "moving.conf"
    target.write_text("a = 1")
    verdict = AntiHallucinationGate().check(
        _ctx(sandbox, observed_hashes={str(target): "older_hash"})
    )
    assert verdict.allowed is True and verdict.repair == f"re-read {target}"


# ------------------------------------------------------------------ sandbox gate


def test_sandbox_blocks_paths_outside_the_arm(sandbox):
    verdict = SandboxGate().check(_ctx(sandbox, command="cat /etc/passwd"))
    assert verdict.allowed is False and "outside the sandbox" in verdict.reason


def test_sandbox_allows_paths_inside(sandbox):
    assert (
        SandboxGate()
        .check(_ctx(sandbox, command=f"cat {sandbox}/deliverable.json"))
        .allowed
        is True
    )


def test_sandbox_repairs_destructive_flags_instead_of_refusing(sandbox):
    verdict = SandboxGate().check(_ctx(sandbox, command="git reset --hard"))
    assert verdict.allowed is True and verdict.repair == "git reset"


def test_sandbox_blocks_parent_traversal(sandbox):
    assert (
        SandboxGate().check(_ctx(sandbox, command="cat ../secrets.json")).allowed
        is False
    )


# ------------------------------------------------------------------- the engine


def test_engine_runs_only_the_stages_gates(sandbox):
    engine = GateEngine()
    # a pre_tool gate must not fire in a stop context
    assert engine.run(_ctx(sandbox, stage="stop")).allowed is True


def test_engine_first_block_wins_and_stops_the_run(sandbox):
    engine = GateEngine(gates=[CompletionGate(), OutputValidationGate()])
    ctx = _ctx(sandbox, stage="stop", deliverable=sandbox / "missing.json")
    verdict = engine.run(ctx)
    assert verdict.allowed is False and verdict.gate == "completion"


def test_engine_records_repairs(sandbox):
    engine = GateEngine()
    engine.run(_ctx(sandbox, command="git reset --hard"))
    assert engine.repairs and engine.repairs[0]["gate"] == "sandbox"


def test_there_is_no_warn_outcome():
    """A gate that warns is a gate the model learns to ignore. ALLOW is the only pass."""
    assert ALLOW.allowed is True
    assert not Verdict(False, "blocked").allowed


# --- added to close the remaining branches ---


def test_output_validation_blocks_when_no_deliverable_declared(sandbox):
    verdict = OutputValidationGate().check(
        _ctx(sandbox, stage="stop", deliverable=None)
    )
    assert verdict.allowed is False and "nothing to validate" in verdict.reason


def test_output_validation_blocks_whitespace_only(sandbox):
    blank = sandbox / "blank.json"
    blank.write_text("   \n\t ")
    verdict = OutputValidationGate().check(
        _ctx(sandbox, stage="stop", deliverable=blank)
    )
    assert verdict.allowed is False and "empty" in verdict.reason


def test_anti_hallucination_allows_an_unchanged_observed_hash(sandbox):
    target = sandbox / "stable.conf"
    target.write_text("a = 1")
    verdict = AntiHallucinationGate().check(
        _ctx(sandbox, observed_hashes={str(target): _sha(target)})
    )
    assert verdict.allowed is True and verdict.repair is None


def test_anti_hallucination_ignores_a_file_that_disappeared(sandbox):
    """A claimed read whose file is gone has no hash to compare - not a staleness false positive."""
    verdict = AntiHallucinationGate().check(
        _ctx(sandbox, observed_hashes={str(sandbox / "gone.conf"): "older"})
    )
    assert verdict.allowed is True


def test_file_hash_helper_returns_none_for_a_directory(sandbox):
    from harness.gates import _file_hash

    assert _file_hash(sandbox) is None
