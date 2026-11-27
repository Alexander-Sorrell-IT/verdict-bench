#!/usr/bin/env python3
# verdict-bench runner/hypothesis.py
#
# Hypothesis Layer - for truly unknown worlds.
#
# propose -> gate -> execute -> evaluate, one loop, and every verdict recorded:
# the model proposes ONE shell command, the command runs inside a sandbox, the
# model evaluates the result, and the loop repeats until the model commits an
# ANSWER or the attempts run out. An unresolved task returns None - a failed
# arm returns an empty answer, never a fabricated one.
#
# What this file used to do, and why it was rewritten (D.1):
#   * it called qwen2.5-coder over local Ollama - a different model from the
#     graded nemotron-relay path, so any number it produced described a model
#     the benchmark never claims. The client now comes from spec/providers.json
#     through harness/keys.py; Ollama survives behind --local for debugging.
#   * its prompt shipped "MANDATORY COMMANDS FOR EACH TASK TYPE (copy exactly)"
#     with per-family command pipelines - the exact token that invalidated the
#     committed 25/25, plus the family taxonomy A.1 removed from the manifest.
#     A prompt that hands over the answer formula measures nothing.
#   * it executed each command twice (a copy-paste defect: two execute_command
#     calls back to back), it reached into engine._current, it carried ~25
#     debug prints, and it auto-accepted any stdout that parsed as JSON as the
#     answer - family-blind fabrication.
#   * it ran commands with cwd=WORLD_DIR, the directory that holds the withheld
#     private root: the arm could read the gold. Commands now run in a sandbox
#     (world_files + manifest copied out), and commands that try to leave it
#     (.., absolute paths, private) are refused before execution.

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR / "harness"))

from harness.keys import (  # noqa: E402
    default_provider_id,
    load_providers,
    resolve_api_key,
)
from harness.model import ModelClient, ModelError  # noqa: E402

WORLD_DIR = BENCH_DIR / "world"
MAX_RETRIES = 3

# The commands an arm may propose. Everything else is refused before execution:
# no interpreters (a shell one-liner is not a benchmark answer), no traversal.
COMMAND_WHITELIST = (
    "ls",
    "find",
    "cat",
    "grep",
    "head",
    "tail",
    "awk",
    "sed",
    "cut",
    "sort",
    "uniq",
    "wc",
    "tr",
    "rev",
    "sha256sum",
    "echo",
    "cd",
    "pwd",
)
FORBIDDEN_FRAGMENTS = ("..", "/world/private", "private/", "gold_world")

SYSTEM_PROMPT = (
    "You are an agent in an unknown environment. Discover how it works by "
    "running shell commands. Output ONLY the next shell command to run - no "
    "explanations, no markdown, no JSON. If you output anything else, your "
    "response is rejected."
)


# ------------------------------------------------------------------ model access


def local_client() -> ModelClient:
    """The debug path: local Ollama. Never the graded route."""
    return ModelClient(
        base_url="http://127.0.0.1:11434/v1",
        api_key="ollama",
        model="qwen2.5-coder:14b",
    )


def relay_client() -> ModelClient:
    """The graded route: the shared relay's default provider and model."""
    providers_path = BENCH_DIR / "spec" / "providers.json"
    providers = load_providers(providers_path)
    provider_id = default_provider_id(providers_path)
    if not provider_id or provider_id not in providers:
        raise ModelError(f"no default provider in {providers_path}")
    provider = providers[provider_id]
    if not provider.default_model:
        raise ModelError(f"provider {provider_id!r} names no default model")
    return ModelClient(
        base_url=provider.base_url,
        api_key=resolve_api_key(provider),
        model=provider.default_model,
    )


def call_model(prompt: str, client: ModelClient, max_tokens: int = 500) -> str:
    """One model call. Retries live inside the client; a dead endpoint raises,
    and the caller records the attempt honestly.

    The default allowance is deliberately generous: the graded model is a
    REASONING model, and at ~200 tokens it can spend the whole budget thinking
    and return a blank completion - which ModelClient refuses to fabricate
    around, ending the attempt honestly. Budget here is not a tuning knob; it
    is the difference between measuring the model and measuring the truncation.
    """
    return client.complete(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
    )


# ------------------------------------------------------------------ sandbox


def build_sandbox(world_dir: Path = WORLD_DIR) -> Path:
    """A sandbox holding exactly the agent-visible tree: world_files and the
    manifest. The withheld private root stays where it is - outside."""
    sandbox = Path(tempfile.mkdtemp(prefix="verdict-sandbox-"))
    shutil.copytree(world_dir / "world_files", sandbox / "world_files")
    shutil.copy(world_dir / "world_manifest.json", sandbox / "world_manifest.json")
    return sandbox


def cleanup_sandbox(sandbox: Optional[Path]) -> None:
    if sandbox is not None and sandbox.is_dir():
        shutil.rmtree(sandbox, ignore_errors=True)


def extract_command(reply: str) -> Optional[str]:
    """The first whitelisted command in a model reply, or None.

    Refuses traversal and private-root fragments before anything executes: the
    sandbox is the tree the arm may read, and a command that tries to leave it
    is not a command, it is an exfiltration attempt.
    """
    text = reply.replace("```", "").replace("`", "").strip()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # The arm's world is the sandbox: absolute paths and home-relative paths
        # are escapes, and private-root fragments are the gold. Both refused,
        # wherever they appear in the command.
        tokens = line.split()
        if any(token.startswith(("/", "~")) for token in tokens):
            return None
        if any(fragment in line for fragment in FORBIDDEN_FRAGMENTS):
            return None
        if tokens and tokens[0] in COMMAND_WHITELIST:
            return line
    return None


def execute_command(
    cmd: str, timeout: int = 30, cwd: Optional[Path] = None
) -> Dict[str, Any]:
    """Execute one command inside the sandbox and return the structured result."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        return {
            "cmd": cmd,
            "exit_code": result.returncode,
            "stdout": result.stdout[:3000],
            "stderr": result.stderr[:3000],
            "ok": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "cmd": cmd,
            "exit_code": -2,
            "stdout": "",
            "stderr": f"timeout after {timeout}s",
            "ok": False,
        }
    except Exception as exc:  # noqa: BLE001 - a dead command ends the attempt
        return {
            "cmd": cmd,
            "exit_code": -3,
            "stdout": "",
            "stderr": f"error: {exc}",
            "ok": False,
        }


# ------------------------------------------------------------------ the record


class HypothesisCycle:
    """One hypothesis -> test -> correction cycle."""

    def __init__(self, hypothesis: str, task_id: str):
        self.hypothesis = hypothesis
        self.task_id = task_id
        self.tests: List[Dict[str, Any]] = []
        self.corrections: List[Dict[str, str]] = []
        self.resolved = False
        self.final_answer: Optional[str] = None
        self.created_at = datetime.now(timezone.utc).isoformat() + "Z"

    def add_test(
        self, action: str, result: Dict[str, Any], matches_expectation: bool
    ) -> None:
        self.tests.append(
            {
                "action": action,
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
                "result": result,
                "matches_expectation": matches_expectation,
            }
        )

    def add_correction(self, correction: str) -> None:
        self.corrections.append(
            {
                "correction": correction,
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            }
        )

    def to_markdown(self) -> str:
        lines: List[str] = []
        lines.append(f"## Hypothesis: {self.hypothesis}")
        lines.append(f"Task: {self.task_id}")
        lines.append(f"Created: {self.created_at}")
        lines.append("")
        for i, test in enumerate(self.tests, 1):
            lines.append(f"### Test {i}: {test['action']}")
            lines.append(f"Matches: {test['matches_expectation']}")
            lines.append(f"Exit: {test['result'].get('exit_code')}")
            lines.append(f"Out: {test['result'].get('stdout', '')[:300]}")
            lines.append(f"Err: {test['result'].get('stderr', '')[:300]}")
            lines.append("")
        if self.corrections:
            lines.append("### Corrections")
            for c in self.corrections:
                lines.append(f"- {c['correction']}")
            lines.append("")
        lines.append(f"Resolved: {self.resolved}")
        if self.final_answer:
            lines.append(f"Answer: {self.final_answer[:500]}")
        lines.append("")
        return "\n".join(lines)


class HypothesisEngine:
    """Manages hypothesis cycles for unknown-world tasks."""

    def __init__(self, log_path: Path = Path("hypothesis_log.md")):
        self.log_path = log_path
        self.cycles: List[HypothesisCycle] = []

    def start_cycle(self, hypothesis: str, task_id: str) -> HypothesisCycle:
        cycle = HypothesisCycle(hypothesis, task_id)
        self.cycles.append(cycle)
        return cycle

    def write_log(self) -> Path:
        lines: List[str] = []
        lines.append("# Hypothesis Log")
        lines.append("")
        lines.append(f"Generated: {datetime.now(timezone.utc).isoformat() + 'Z'}")
        lines.append(f"Total cycles: {len(self.cycles)}")
        lines.append(f"Resolved: {sum(1 for c in self.cycles if c.resolved)}")
        lines.append("")
        for cycle in self.cycles:
            lines.append(cycle.to_markdown())
        self.log_path.write_text("\n".join(lines))
        return self.log_path

    def recovery_rate(self) -> float:
        """LEGACY metric: cycles resolved within <=3 tests. Kept for
        comparability with the recorded World-1 attempts; the measured
        recovery counter (D.4) replaces it and prints this as `legacy:`."""
        if not self.cycles:
            return 1.0
        recovered = 0
        for cycle in self.cycles:
            if cycle.resolved and len(cycle.tests) <= 3:
                recovered += 1
        return recovered / len(self.cycles)


# ------------------------------------------------------------------ the loop


def _propose_prompt(
    task_description: str,
    discovery_text: str,
    cycle: HypothesisCycle,
    attempt: int,
    max_attempts: int,
) -> str:
    parts = [
        "TASK DESCRIPTION:",
        task_description,
        "",
        "ENVIRONMENT DISCOVERY:",
        discovery_text[:2000],
        "",
        f"ATTEMPT {attempt + 1}/{max_attempts}",
        "Previous results:",
    ]
    for test in cycle.tests:
        parts.append(
            f"  Command: {test['action']}\n"
            f"  Exit: {test['result'].get('exit_code')}\n"
            f"  Output: {test['result'].get('stdout', '')[:300]}\n"
            f"  Error: {test['result'].get('stderr', '')[:200]}"
        )
    parts.append("")
    parts.append(
        "Output only the next shell command to run - one line, no explanation. "
        "Read only what is inside this sandbox."
    )
    return "\n".join(parts)


def _evaluate_prompt(task_description: str, cmd: str, result: Dict[str, Any]) -> str:
    return "\n".join(
        [
            "TASK DESCRIPTION:",
            task_description,
            "",
            f"COMMAND EXECUTED: {cmd}",
            f"EXIT CODE: {result['exit_code']}",
            f"STDOUT: {result['stdout'][:800]}",
            f"STDERR: {result['stderr'][:500]}",
            "",
            "Respond with EXACTLY one line:",
            '  ANSWER: {"<key>": "<the value you found>"}   if the output contains '
            "the answer",
            "  COMMAND: <shell command>                     if you need another "
            "command",
            "",
            "Never guess. If the output does not contain the answer, ask for the "
            "next command.",
        ]
    )


def _parse_answer(decision: str) -> Optional[str]:
    """The model's committed answer, or None. Only an ANSWER: line counts - and
    only if it parses as a JSON object, so a prose answer never becomes a
    fabricated JSON one."""
    text = decision.replace("```", "").strip()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ANSWER:"):
            payload = line[len("ANSWER:") :].strip()
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return json.dumps(parsed, sort_keys=True)
            return None
    return None


def run_hypothesis_task(
    task_description: str,
    task_id: str,
    engine: HypothesisEngine,
    max_attempts: int = 5,
    discovery_text: str = "",
    client: Optional[ModelClient] = None,
    sandbox: Optional[Path] = None,
) -> Optional[str]:
    """Run one task: propose -> execute -> evaluate, until an ANSWER or exhaustion.

    Returns the model's committed answer, or None. None means unresolved, and
    the caller records an empty answer - never a synthesis.
    """
    client = client or relay_client()
    owns_sandbox = sandbox is None
    sandbox = sandbox or build_sandbox()

    cycle = engine.start_cycle(
        hypothesis="Initial: probe the environment to discover task requirements.",
        task_id=task_id,
    )

    try:
        for attempt in range(max_attempts):
            try:
                reply = call_model(
                    _propose_prompt(
                        task_description, discovery_text, cycle, attempt, max_attempts
                    ),
                    client,
                )
            except ModelError as exc:
                cycle.add_correction(f"model call failed: {exc}")
                continue

            cmd = extract_command(reply)
            if cmd is None:
                cycle.add_correction(f"no whitelisted command in reply: {reply[:120]}")
                continue

            result = execute_command(cmd, cwd=sandbox)
            cycle.add_test(cmd, result, result["ok"] and bool(result["stdout"].strip()))

            try:
                decision = call_model(
                    _evaluate_prompt(task_description, cmd, result),
                    client,
                    max_tokens=2000,
                )
            except ModelError as exc:
                cycle.add_correction(f"model evaluation failed: {exc}")
                continue

            answer = _parse_answer(decision)
            if answer is not None:
                cycle.resolved = True
                cycle.final_answer = answer
                return answer

            if "COMMAND:" in decision:
                next_cmd = decision.split("COMMAND:", 1)[1].strip().splitlines()[0]
                cycle.add_correction(f"model requested another command: {next_cmd}")
            else:
                cycle.add_correction(f"unclear decision: {decision[:120]}")
    finally:
        if owns_sandbox:
            cleanup_sandbox(sandbox)

    cycle.resolved = False
    return None


if __name__ == "__main__":
    engine = HypothesisEngine()
    answer = run_hypothesis_task(
        "There is a configuration file somewhere in the world_files directory. "
        "Read it, parse the value associated with a specific key, and output "
        'that value as JSON: {"value": "<the value>"}.',
        "smoke-task",
        engine,
    )
    path = engine.write_log()
    print(f"Answer: {answer}")
    print(f"Log written to {path}")
    print(f"Recovery rate (legacy): {engine.recovery_rate():.0%}")
