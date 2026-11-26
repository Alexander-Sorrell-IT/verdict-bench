#!/usr/bin/env python3
# verdict-bench harness/minimal_arm.py
#
# The in-repo arm. Replaces shelling out to an external harness, because "OmniCLI as
# shipped" turned out to mean a dirty submodule working tree that no clone could reproduce.
#
# Loop: propose -> gate -> execute -> gate -> evaluate -> stop, until an answer parses,
# the budget runs out, or the model stops responding.
#
# THE RULE THAT MATTERS: if the arm does not produce a parseable answer, it returns an
# empty one. It never fabricates. The retracted 25/25 run answered every task with the
# string "Task W2-NNN: discovered via hypothesis testing. Attempts: 2/5" - a synthesized
# placeholder that passed a non-empty check and failed the grader 0/25. That path does
# not exist here: a failed arm scores zero, which is what the truth looks like.
#
# Two arms share this loop: `run` (minimal) and `run_gated` (AF2 gates injected). The
# gates live outside the loop so the two arms differ only by the gate set, which is the
# whole controlled variable.

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from harness import gates as gate_module
from harness.budget import Budget
from harness.gates import GateContext, GateEngine

SYSTEM_PROMPT = (
    "You are an agent working in an environment you have not seen before. "
    "Discover how it works by running shell commands, one at a time. "
    "Reply with exactly one line: either the next command to run, or "
    "ANSWER: <json> when you have the value the task asked for."
)

# Commands an arm may run, and nothing else. A model that proposes `rm -rf /` has
# misread the task, and the whitelist stops that from being a run-wide event.
COMMAND_PREFIXES = (
    "ls",
    "find",
    "cat",
    "head",
    "tail",
    "grep",
    "echo",
    "pwd",
    "cd",
    "python3",
    "awk",
    "sed",
    "cut",
    "sort",
    "uniq",
    "wc",
    "file",
    "sha256sum",
    "md5sum",
    "tr",
    "rev",
    "env",
    "which",
    "stat",
    "diff",
)

MAX_ATTEMPTS = 5


class Attempt:
    """One arm's attempt on one task, with the budget it actually consumed."""

    def __init__(self, task_id: str, arm: str) -> None:
        self.task_id = task_id
        self.arm = arm
        self.answer: str = ""
        self.steps = 0
        self.model_calls = 0
        self.wall_seconds = 0.0
        self.overflowed = False
        self.stopped_reason = "incomplete"
        self.gate_blocks: List[str] = []
        self.repairs: List[Dict[str, str]] = []

    def consumed(self) -> float:
        return self.wall_seconds

    def as_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "arm": self.arm,
            "answer": self.answer,
            "steps": self.steps,
            "model_calls": self.model_calls,
            "wall_seconds": round(self.wall_seconds, 3),
            "overflowed": self.overflowed,
            "stopped_reason": self.stopped_reason,
            "gate_blocks": self.gate_blocks,
            "repairs": self.repairs,
        }


def _extract_command(text: str) -> Optional[str]:
    """Pull one shell command out of a model reply. Returns None if there isn't one.

    Deliberately strict: a reply the arm cannot parse is not turned into a command, and
    never into a guess.
    """
    for raw in text.splitlines():
        line = raw.strip().strip("`").strip()
        if not line:
            continue
        line = re.sub(r"^(ANSWER|COMMAND)\s*:\s*", "", line).strip()
        first = line.split()[0] if line.split() else ""
        if first in COMMAND_PREFIXES:
            return line
    return None


def _extract_answer(text: str) -> Optional[str]:
    """Pull an ANSWER payload out of a model reply, strictly."""
    match = re.search(r"ANSWER:\s*(\{.*\}|\[.*\])\s*$", text.strip(), re.DOTALL)
    if not match:
        return None
    payload = match.group(1)
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return json.dumps(parsed, sort_keys=True)


def _execute(command: str, sandbox: Path, timeout: float = 60.0) -> Dict[str, Any]:
    """Run one command inside the sandbox and report what happened.

    shell=True is required - the task format is pipes (grep | awk | head), which is the
    point of the benchmark. The mitigations are structural, not comments:
      1. the command must start with a whitelisted prefix (COMMAND_PREFIXES)
      2. SandboxGate runs BEFORE this function and blocks anything reaching outside
         the sandbox, and strips destructive flags
      3. cwd is pinned to the sandbox
    # nosec B404 B602 - guarded by the whitelist + SandboxGate, see harness/gates.py
    """
    import subprocess  # nosec B404

    try:
        result = subprocess.run(
            command,
            shell=True,  # nosec B602 - whitelisted prefix + SandboxGate, see harness/gates.py
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(sandbox),
        )
        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:2000],
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "exit_code": -2,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
        }
    except Exception as exc:  # noqa: BLE001 - an execution failure must be reported
        return {
            "command": command,
            "exit_code": -3,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
        }


def run_task(
    task: Dict[str, Any],
    client: Any,
    sandbox: Path,
    engine: Optional[GateEngine],
    arm: str,
    budget: Optional[Budget] = None,
    required_keys: Tuple[str, ...] = (),
    max_attempts: int = MAX_ATTEMPTS,
) -> Attempt:
    """Run one task with one arm. Returns an Attempt, never a synthesized answer."""
    budget = budget or Budget(max_steps=50, max_model_calls=40, max_wall_seconds=600.0)
    attempt = Attempt(task["task_id"], arm)
    started = time.perf_counter()

    context = GateContext(
        stage="turn_start",
        sandbox=sandbox,
        deliverable=sandbox / "deliverable.json",
        required_keys=required_keys,
        reads_this_turn=0,
        last_files_read=(),
    )
    if engine is not None:
        verdict = engine.run(context)
        if not verdict.allowed:
            attempt.gate_blocks.append(f"turn_start/{verdict.gate}: {verdict.reason}")
            attempt.stopped_reason = "blocked_before_start"
            attempt.wall_seconds = time.perf_counter() - started
            return attempt

    history: List[Dict[str, str]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _task_prompt(task["description"], sandbox)},
    ]
    reads_this_turn = 0
    last_files_read: List[str] = []
    # Why the arm failed, when it fails. Recorded so a run's log explains itself instead
    # of collapsing every failure into "budget exhausted".
    last_failure: Optional[str] = None

    for step in range(1, max_attempts + 1):
        attempt.steps = step
        context = GateContext(
            stage="model_call",
            sandbox=sandbox,
            model_call_seconds=0.0,
        )
        if engine is not None:
            verdict = engine.run(context)
            if not verdict.allowed:
                attempt.gate_blocks.append(
                    f"model_call/{verdict.gate}: {verdict.reason}"
                )
                break

        try:
            reply = client.complete(history)
        except Exception as exc:  # noqa: BLE001 - a model failure ends the attempt
            last_failure = f"model_error: {type(exc).__name__}"
            break
        attempt.model_calls = getattr(client, "call_count", step)
        history.append({"role": "assistant", "content": reply})

        answer = _extract_answer(reply)
        if answer is not None:
            attempt.answer = answer
            attempt.stopped_reason = "answered"
            break

        command = _extract_command(reply)
        if command is None:
            last_failure = "unparseable_reply"
            history.append(
                {
                    "role": "user",
                    "content": "That reply was not a command or an ANSWER. Reply with exactly "
                    "one shell command, or ANSWER: <json>.",
                }
            )
            continue

        tool_context = GateContext(
            stage="pre_tool",
            sandbox=sandbox,
            command=command,
            reads_this_turn=reads_this_turn,
            last_files_read=tuple(last_files_read),
            deliverable=sandbox / "deliverable.json",
            required_keys=required_keys,
        )
        if engine is not None:
            verdict = engine.run(tool_context)
            if not verdict.allowed:
                attempt.gate_blocks.append(f"pre_tool/{verdict.gate}: {verdict.reason}")
                continue
            if verdict.repair:
                attempt.repairs.append({"gate": verdict.gate, "action": verdict.repair})
                command = verdict.repair

        result = _execute(command, sandbox)
        post_context = GateContext(stage="post_tool", sandbox=sandbox, command=command)
        if engine is not None:
            engine.run(post_context)

        if command.split() and command.split()[0] in ("cat", "head", "tail", "find"):
            reads_this_turn += 1
            last_files_read.append(command)
        else:
            reads_this_turn = 0
            last_files_read = []

        history.append(
            {
                "role": "user",
                "content": (
                    f"$ {command}\nexit {result['exit_code']}\n"
                    f"stdout:\n{result['stdout'][:1200]}\n"
                    f"stderr:\n{result['stderr'][:600]}\n\n"
                    "Reply with the next command, or ANSWER: <json>."
                ),
            }
        )

        if step >= max_attempts:
            attempt.stopped_reason = "attempt_budget_exhausted"

    # Stop gates only once there is something to stop with.
    stop_context = GateContext(
        stage="stop",
        sandbox=sandbox,
        deliverable=sandbox / "deliverable.json",
        required_keys=required_keys,
    )
    if engine is not None:
        verdict = engine.run(stop_context)
        if not verdict.allowed:
            attempt.gate_blocks.append(f"stop/{verdict.gate}: {verdict.reason}")
            attempt.stopped_reason = "stopped_by_gate"

    if not attempt.answer and last_failure:
        attempt.stopped_reason = last_failure

    attempt.wall_seconds = time.perf_counter() - started
    if (
        attempt.steps > budget.max_steps
        or attempt.model_calls > budget.max_model_calls
        or attempt.wall_seconds > budget.max_wall_seconds
    ):
        attempt.overflowed = True
    return attempt


def _task_prompt(description: str, sandbox: Path) -> str:
    """The task prompt. Deliberately contains no answer formula, no tool taxonomy and no
    naming of the task family - those are exactly what contaminated the last benchmark.
    """
    return (
        f"TASK: {description}\n\n"
        f"Your working directory is {sandbox}. The data is in {sandbox}.\n"
        "Consult {dir} for the files. Work autonomously; do not ask questions.\n"
        "When you have the value, reply ANSWER: <json>."
    ).replace("{dir}", str(sandbox))


def run(
    task: Dict[str, Any], budget: Any, sandbox_path: str, client: Any = None
) -> Attempt:
    """Arm entrypoint: MINIMAL. No gates. Same loop, same budget, one variable changed."""
    from harness.budget import Budget

    resolved = budget if isinstance(budget, Budget) else Budget(**dict(budget))
    return run_task(
        task, client, Path(sandbox_path), engine=None, arm="MINIMAL", budget=resolved
    )


def run_gated(
    task: Dict[str, Any], budget: Any, sandbox_path: str, client: Any = None
) -> Attempt:
    """Arm entrypoint: GATED. AF2 gate set injected. Identical otherwise."""
    resolved = budget if isinstance(budget, Budget) else Budget(**dict(budget))
    engine = GateEngine(gate_module.default_gates())
    return run_task(
        task, client, Path(sandbox_path), engine=engine, arm="GATED", budget=resolved
    )
