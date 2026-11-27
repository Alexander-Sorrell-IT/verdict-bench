"""The Harness²-shaped arm (C): propose → probe → compose, lessons threaded,
rows in the same shape as every other arm.

The external adapter runs OpenCode-in-docker against the harness2 package;
this repo ports the SHAPE only and executes nothing external (B.3's rule, and
standalone since the keys work).
"""

import sys
from pathlib import Path
from typing import Any, Dict, List


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "world"))

from harness2_adapter.h2_world import run_family, run_task  # noqa: E402
from hypothesis import build_sandbox  # noqa: E402


class ScriptedClient:
    """Replies from a script: probe commands, then a compose, then a lesson."""

    def __init__(self, compose_answer: str):
        self.script = [
            "cat world_files/W2-000.conf",
            "ls world_files",
            'ANSWER: {"value": "value_2022"}',
        ]
        self.compose_answer = compose_answer
        self.calls: List[List[Dict[str, str]]] = []

    model = "stub-model"
    base_url = "http://stub.invalid/v1"

    def complete(self, messages: List[Dict[str, str]], max_tokens: int = 500) -> str:
        self.calls.append(messages)
        prompt = messages[-1]["content"]
        if "answer now" in prompt:
            return f"ANSWER: {self.compose_answer}"
        if "What did you learn" in prompt:
            return "the .conf file's key=value line is the whole answer"
        if self.script:
            return self.script.pop(0)
        return 'ANSWER: {"value": "value_2022"}'


def _task(task_id: str = "W2-000") -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "family": "config-parsing",
        "description": (
            "There is a configuration file somewhere in the world_files "
            "directory. Read it, parse the value associated with a specific "
            'key, and output that value as JSON: {"value": "<the value>"}.\n\n'
            "Your task's evidence file is world_files/W2-000.conf."
        ),
        "evidence_file": "W2-000.conf",
    }


def test_a_correct_compose_passes_and_is_graded_in_code():
    client = ScriptedClient('{"value": "value_2022"}')
    sandbox = build_sandbox()
    try:
        row = run_task(_task(), client, sandbox, lessons=[])
    finally:
        from hypothesis import cleanup_sandbox

        cleanup_sandbox(sandbox)
    assert row["arm"] == "C"
    assert row["task_pass"] is True
    assert row["answer"] == '{"value": "value_2022"}'
    assert row["probes" if "probes" in row else "history"]  # the probes are recorded


def test_a_wrong_compose_fails_without_fabricating():
    client = ScriptedClient('{"value": "confidently wrong"}')
    sandbox = build_sandbox()
    try:
        row = run_task(_task(), client, sandbox, lessons=[])
    finally:
        from hypothesis import cleanup_sandbox

        cleanup_sandbox(sandbox)
    assert row["task_pass"] is False
    assert row["criteria_passed"] == 0
    assert row["reason"]


def test_lessons_thread_across_a_family():
    client = ScriptedClient('{"value": "value_2022"}')
    sandbox = build_sandbox()
    try:
        rows = run_family(
            "config-parsing", [_task("W2-000")], client, sandbox, probes=2
        )
    finally:
        from hypothesis import cleanup_sandbox

        cleanup_sandbox(sandbox)
    assert len(rows) == 1
    # the lesson prompt was asked after the task - the threading happened
    asked_lessons = [
        m["content"]
        for call in client.calls
        for m in call
        if "What did you learn" in m["content"]
    ]
    assert asked_lessons, "the family loop never asked for a lesson"


def test_the_rows_carry_the_route_and_the_stats_fields():
    client = ScriptedClient('{"value": "value_2022"}')
    sandbox = build_sandbox()
    try:
        row = run_task(_task(), client, sandbox, lessons=[])
    finally:
        from hypothesis import cleanup_sandbox

        cleanup_sandbox(sandbox)
    for field in (
        "arm",
        "task_id",
        "task_pass",
        "criteria_passed",
        "criteria_total",
        "valid_json",
        "model",
        "base_url",
    ):
        assert field in row, field
