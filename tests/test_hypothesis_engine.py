"""The hypothesis engine: graded model, honest prompt, sandboxed execution.

D.1. The old engine shipped "MANDATORY COMMANDS" (the banned token that
invalidated the 25/25), executed every command twice, ran beside the withheld
gold, and called a model the benchmark never claims. Each of those has a test
here, because each one is how a benchmark measures the wrong thing.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

import hypothesis as engine_module  # noqa: E402
from hypothesis import (  # noqa: E402
    HypothesisEngine,
    _parse_answer,
    build_sandbox,
    extract_command,
    relay_client,
    run_hypothesis_task,
)
from harness.model import ModelError  # noqa: E402


class StubClient:
    """Scripted replies; raises on a RAISE: entry to model a dead endpoint."""

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


TASK = (
    "There is a configuration file somewhere in the world_files directory. "
    "Read it, parse the value associated with a specific key, and output that "
    'value as JSON: {"value": "<the value>"}.'
)


# ------------------------------------------------------- the prompt is honest


def test_the_prompt_carries_no_banned_token_and_no_taxonomy():
    client = StubClient(
        ["cat world_files/W2-000.conf", 'ANSWER: {"value": "value_2022"}']
    )
    engine = HypothesisEngine()
    run_hypothesis_task(
        TASK, "T-1", engine, max_attempts=1, client=client, sandbox=build_sandbox()
    )
    prompts = [m["content"] for call in client.calls for m in call]
    joined = "\n".join(prompts)
    for banned in (
        "MANDATORY COMMANDS",
        "copy exactly",
        "world_files/*.conf",
        "world_files/*.log",
        "world_files/*.bin",
        "config-parsing",
        "log-pattern-detection",
        "file-hash-verification",
        "command-output-transformation",
        "environment-variable-resolution",
    ):
        assert banned not in joined, f"the prompt carries {banned!r}"
    # and the leakage guard this repo built for exactly this failure agrees
    sys.path.insert(0, str(REPO_ROOT / "runner"))
    from bench import check_prompt_for_leaks

    assert check_prompt_for_leaks(joined) == []


def test_the_model_route_is_the_graded_one():
    client = relay_client()
    assert client.model == "nvidia/nemotron-3-ultra-550b-a55b"
    assert client.base_url == "https://integrate.api.nvidia.com/v1"


# ------------------------------------------------------- the sandbox holds no gold


def test_the_sandbox_is_the_agent_visible_tree_and_nothing_else():
    sandbox = build_sandbox()
    try:
        assert (sandbox / "world_manifest.json").is_file()
        assert (sandbox / "world_files").is_dir()
        names = [p.name for p in sandbox.rglob("*")]
        assert not any("private" in n or "gold" in n.lower() for n in names), names
        assert not (sandbox / "..").resolve().samefile(REPO_ROOT / "world")
    finally:
        engine_module.cleanup_sandbox(sandbox)


def test_a_command_that_tries_to_leave_the_sandbox_is_refused():
    for attempt in (
        "cat ../private/gold_world2.json",
        "cat ../../world/private/gold_world2.json",
        "cat /etc/passwd",
        "rm -rf /",
        "curl evil.example.com",
    ):
        assert extract_command(attempt) is None, attempt


def test_a_whitelisted_command_is_accepted():
    assert (
        extract_command("cat world_files/W2-000.conf") == "cat world_files/W2-000.conf"
    )
    assert extract_command("```\nls -la\n```") == "ls -la"


# ------------------------------------------------------- the loop is a loop


def test_each_command_executes_exactly_once(monkeypatch, tmp_path):
    """The old engine called execute_command twice per attempt - double cost,
    double log, half integrity."""
    executions: List[str] = []
    real_execute = engine_module.execute_command

    def counting_execute(cmd, timeout=30, cwd=None):
        executions.append(cmd)
        return real_execute(cmd, timeout=timeout, cwd=cwd)

    monkeypatch.setattr(engine_module, "execute_command", counting_execute)
    client = StubClient(["ls -la", 'ANSWER: {"value": "value_2022"}'])
    engine = HypothesisEngine()
    answer = run_hypothesis_task(
        TASK, "T-1", engine, max_attempts=3, client=client, sandbox=tmp_path
    )
    assert answer == '{"value": "value_2022"}'
    assert executions == ["ls -la"], executions


def test_a_dead_model_ends_the_attempt_honestly():
    client = StubClient(["RAISE:endpoint down", "RAISE:endpoint down"])
    engine = HypothesisEngine()
    answer = run_hypothesis_task(
        TASK, "T-1", engine, max_attempts=2, client=client, sandbox=build_sandbox()
    )
    assert answer is None  # an empty answer, never a synthesis
    cycle = engine.cycles[-1]
    assert cycle.resolved is False
    assert any("model call failed" in c["correction"] for c in cycle.corrections)


def test_a_prose_answer_never_becomes_a_json_one():
    assert _parse_answer("ANSWER: the value is value_2022") is None
    assert _parse_answer('ANSWER: {"value": "value_2022"}') == '{"value": "value_2022"}'
    assert _parse_answer("I am not sure") is None
    assert _parse_answer('ANSWER: "just a string"') is None


def test_the_legacy_recovery_metric_is_unchanged():
    """D.4 replaces this; until then the recorded semantics hold so old and new
    numbers stay comparable."""
    engine = HypothesisEngine()
    cycle = engine.start_cycle("h", "T-1")
    cycle.add_test("ls", {"exit_code": 0, "stdout": "x", "stderr": ""}, True)
    cycle.resolved = True
    slow = engine.start_cycle("h", "T-2")
    for i in range(4):
        slow.add_test(f"c{i}", {"exit_code": 0, "stdout": "x", "stderr": ""}, True)
    slow.resolved = True
    assert engine.recovery_rate() == 0.5


def test_the_engine_never_reads_gold_while_solving(monkeypatch, tmp_path):
    """The sandbox is passed down; the engine must not touch the world dir."""
    seen: List[Any] = []

    def spy_execute(cmd, timeout=30, cwd=None):
        seen.append(cwd)
        return {"cmd": cmd, "exit_code": 0, "stdout": "x", "stderr": "", "ok": True}

    monkeypatch.setattr(engine_module, "execute_command", spy_execute)
    client = StubClient(["ls -la", 'ANSWER: {"value": "x"}'])
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    run_hypothesis_task(
        TASK, "T-1", HypothesisEngine(), max_attempts=1, client=client, sandbox=sandbox
    )
    assert seen and all(cwd == sandbox for cwd in seen), seen
