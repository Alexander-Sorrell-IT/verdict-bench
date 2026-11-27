#!/usr/bin/env python3
# verdict-bench harness/minimal_arm.py
#
# The in-repo arm. Replaces shelling out to an external harness, because a submodule
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

import hashlib
import json
import re
import shlex
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from harness import gates as gate_module
from harness.budget import Budget
from harness.gates import GateContext, GateEngine

SYSTEM_PROMPT = (
    "You are an agent working in an environment you have not seen before. "
    "Discover how it works by running shell commands, one at a time. "
    "Always compute transformations, hashes, counts, and string operations using "
    "shell commands or python3 rather than mentally. "
    "Reply with exactly one line: either the next shell command to run, or "
    "ANSWER: <json> when you have the verified value the task asked for."
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
    "sh",
    "bash",
    "python3",
    "python",
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
    "printenv",
    "which",
    "stat",
    "diff",
    "xargs",
    "base64",
    "jq",
    "od",
    "xxd",
    "hexdump",
)

# Default per-task step cap for direct run_task() callers; run()/run_gated() share
# ARM_MAX_ATTEMPTS so both arms get identical room for verification and recovery.
MAX_ATTEMPTS = 5
ARM_MAX_ATTEMPTS = 8

_TOOL_CALL_KEYS = frozenset({"tool", "command", "cmd", "shell", "function"})
_PROSE_FOLLOWERS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "that",
        "this",
        "these",
        "those",
        "to",
        "of",
        "in",
        "for",
        "with",
        "out",
        "by",
        "from",
        "if",
        "it",
        "we",
        "i",
        "you",
        "will",
        "can",
        "should",
        "must",
    }
)
_AMBIGUOUS_ENGLISH_COMMANDS = frozenset(
    {"find", "file", "sort", "head", "tail", "cut", "cat", "echo", "which", "diff"}
)


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
        self.transport_dead = False
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
            "transport_dead": self.transport_dead,
            "gate_blocks": self.gate_blocks,
            "repairs": self.repairs,
        }


def _is_tool_call_dict(obj: Any) -> bool:
    """Detect tool-call-shaped JSON so it is translated to a command, never an answer."""
    if not isinstance(obj, dict):
        return False
    keys = set(obj.keys())
    if keys & _TOOL_CALL_KEYS:
        return True
    if "name" in keys and ("arguments" in keys or "args" in keys or "input" in keys):
        return True
    return False


def _translate_tool_call(obj: Any) -> Optional[str]:
    """Translate a tool-call-shaped JSON object into a whitelisted shell command."""
    if not isinstance(obj, dict):
        return None

    # Direct command wrapper: {"command": "..."} / {"cmd": "..."} / {"shell": "..."}
    for key in ("command", "cmd", "shell"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return _accept_whitelisted(val.strip())

    # OpenAI function wrapper: {"function": {"name": ..., "arguments": ...}}
    if isinstance(obj.get("function"), dict):
        fn = obj["function"]
        name = str(fn.get("name") or "").strip().lower()
        args = fn.get("arguments", {})
    else:
        name = str(obj.get("tool") or obj.get("name") or "").strip().lower()
        args = obj.get("arguments") or obj.get("args") or obj.get("input") or obj

    if isinstance(args, str):
        stripped = args.strip()
        if stripped.startswith("{"):
            try:
                args = json.loads(stripped)
            except json.JSONDecodeError:
                args = {"command": stripped}
        else:
            args = {"command": stripped}
    if not isinstance(args, dict):
        return None

    # Merge top-level fallback fields when args wasn't nested (e.g. {"tool": "read", "path": "..."})
    merged: Dict[str, Any] = dict(obj)
    merged.update(args)

    if name in (
        "shell",
        "bash",
        "sh",
        "exec",
        "execute",
        "run",
        "run_command",
        "terminal",
        "command",
    ):
        for key in ("command", "cmd", "script", "input"):
            val = merged.get(key)
            if isinstance(val, str) and val.strip():
                return _accept_whitelisted(val.strip())
            if isinstance(val, list) and val:
                joined = " ".join(str(x) for x in val)
                return _accept_whitelisted(joined)
        return None

    if name in ("read", "read_file", "cat", "view", "view_file", "open", "get_file"):
        for key in ("path", "file", "filename", "file_path", "target"):
            val = merged.get(key)
            if isinstance(val, str) and val.strip():
                return _accept_whitelisted(f"cat {shlex.quote(val.strip())}")
        return None

    if name in ("run_script", "execute_script", "sh_exec"):
        for key in ("path", "file", "script"):
            val = merged.get(key)
            if isinstance(val, str) and val.strip():
                return _accept_whitelisted(f"sh {shlex.quote(val.strip())}")
        return None

    if name in ("list", "ls", "list_dir", "list_files"):
        target = ""
        for key in ("path", "dir", "directory"):
            val = merged.get(key)
            if isinstance(val, str) and val.strip():
                target = val.strip()
                break
        cmd = f"ls -la {shlex.quote(target)}" if target else "ls -la"
        return _accept_whitelisted(cmd)

    if name in ("grep", "search"):
        pattern = merged.get("pattern") or merged.get("query")
        path = merged.get("path") or merged.get("file") or "."
        if isinstance(pattern, str) and pattern.strip():
            return _accept_whitelisted(
                f"grep -n {shlex.quote(pattern.strip())} {shlex.quote(str(path))}"
            )
        return None

    return None


def _accept_whitelisted(line: str) -> Optional[str]:
    cleaned = line.strip().strip("`").strip()
    if cleaned.startswith("$ "):
        cleaned = cleaned[2:].strip()
    cleaned = re.sub(r"^COMMAND\s*:\s*", "", cleaned).strip()
    if not cleaned:
        return None
    first = cleaned.split()[0]
    if first in COMMAND_PREFIXES:
        return cleaned
    return None


def _looks_like_prose(line: str) -> bool:
    """Reject multi-word English prose that happens to start with a Unix command word."""
    words = line.split()
    if len(words) < 3:
        return False
    first = words[0]
    if first not in _AMBIGUOUS_ENGLISH_COMMANDS:
        return False
    second = words[1].lower().strip(".,;:")
    # Flags, paths, quotes, or pipes signal a real command; English articles/auxiliaries signal prose.
    if second.startswith(("-", ".", "/", "'", '"')) or "/" in second or "." in second:
        return False
    if second in _PROSE_FOLLOWERS:
        return True
    return False


def _iter_json_objects(text: str) -> List[Any]:
    """Decode any JSON objects/arrays embedded in text, without greedy regexes."""
    decoder = json.JSONDecoder()
    found: List[Any] = []
    idx = 0
    length = len(text)
    while idx < length:
        brace = text.find("{", idx)
        bracket = text.find("[", idx)
        candidates = [p for p in (brace, bracket) if p != -1]
        if not candidates:
            break
        start = min(candidates)
        try:
            obj, end = decoder.raw_decode(text, start)
            found.append(obj)
            idx = end
        except json.JSONDecodeError:
            idx = start + 1
    return found


def _extract_command(text: str) -> Optional[str]:
    """Pull one shell command out of a model reply. Returns None if there isn't one.

    Translates tool-call-shaped JSON replies (e.g. `{"tool": "read", "path": ...}`
    or `{"command": ...}`) into whitelisted shell commands, and reads fenced shell
    blocks cleanly. Non-whitelisted commands and prose guesses stay rejected.
    """
    stripped = text.strip()
    if not stripped:
        return None

    # 1. Tool-call JSON anywhere in the reply (highest-signal fix for reasoning models).
    for obj in _iter_json_objects(stripped):
        if _is_tool_call_dict(obj):
            translated = _translate_tool_call(obj)
            if translated:
                return translated

    # 2. Markdown fenced code blocks (prefer the last non-json block).
    fenced = re.findall(r"```([a-zA-Z0-9_-]*)\s*\n(.*?)```", stripped, re.DOTALL)
    for lang, body in reversed(fenced):
        if lang.lower() == "json":
            continue
        for raw in body.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.upper().startswith("ANSWER:"):
                continue
            cmd = _accept_whitelisted(line)
            if cmd and not _looks_like_prose(cmd):
                return cmd

    # 3. Line-by-line scan for direct command replies.
    for raw in stripped.splitlines():
        line = raw.strip().strip("`").strip()
        if not line or line.startswith("```"):
            continue
        if re.match(r"^ANSWER\s*:", line, re.IGNORECASE):
            continue
        cmd = _accept_whitelisted(line)
        if cmd and not _looks_like_prose(cmd):
            return cmd
    return None


def _extract_answer(text: str) -> Optional[str]:
    """Pull an ANSWER JSON payload out of a model reply, strictly.

    Accepts `ANSWER: <json>` (with or without markdown fences or trailing text),
    or a standalone/fenced JSON object/array that is NOT a tool-call payload.
    Malformed JSON, prose, and guesses remain rejected.
    """
    stripped = text.strip()
    if not stripped:
        return None

    decoder = json.JSONDecoder()

    # 1. Explicit ANSWER: marker (scan from last occurrence to first).
    markers = list(re.finditer(r"ANSWER\s*:", stripped, re.IGNORECASE))
    for match in reversed(markers):
        tail = stripped[match.end() :].lstrip()
        # Strip optional opening fence right after ANSWER:
        tail = re.sub(r"^```(?:json)?\s*", "", tail, flags=re.IGNORECASE).lstrip("`").lstrip()
        if not tail or tail[0] not in "{[":
            continue
        try:
            parsed, _ = decoder.raw_decode(tail, 0)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, (dict, list)) and not _is_tool_call_dict(parsed):
            return json.dumps(parsed, sort_keys=True)
    if markers:
        # Explicit ANSWER: was attempted but failed to parse strictly.
        return None

    # 2. Standalone fenced JSON block (when reply is only the JSON block).
    fenced_json = re.fullmatch(
        r"```(?:json)?\s*\n?(.*?)\n?```", stripped, re.DOTALL | re.IGNORECASE
    )
    candidate_text = fenced_json.group(1).strip() if fenced_json else stripped
    if candidate_text and candidate_text[0] in "{[":
        try:
            parsed = json.loads(candidate_text)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, (dict, list)) and not _is_tool_call_dict(parsed):
            return json.dumps(parsed, sort_keys=True)

    return None


def _infer_required_keys(description: str) -> Tuple[str, ...]:
    """Extract expected top-level JSON keys stated in the public task description."""
    match = re.search(r"JSON\s*:\s*(\{[^}\n]+\})", description)
    if not match:
        return ()
    keys = re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:', match.group(1))
    return tuple(dict.fromkeys(keys))


def _record_read_files(
    command: str,
    sandbox: Path,
    last_files_read: List[str],
    observed_hashes: Dict[str, str],
) -> None:
    """Record sandbox files touched by a read command for AntiHallucinationGate."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens[1:]:
        if token.startswith("-"):
            continue
        candidate = Path(token)
        resolved = candidate if candidate.is_absolute() else (sandbox / candidate)
        try:
            if resolved.is_file():
                resolved.resolve().relative_to(sandbox.resolve())
                key = str(resolved)
                if token not in last_files_read:
                    last_files_read.append(token)
                observed_hashes[key] = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except (ValueError, OSError):
            continue


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
    start_calls = int(getattr(client, "call_count", 0))

    # Clean any stale deliverable from an earlier task sharing the sandbox so
    # CompletionGate and OutputValidationGate always inspect THIS task's output.
    deliverable = sandbox / "deliverable.json"
    if deliverable.exists():
        deliverable.unlink()

    effective_keys = required_keys or _infer_required_keys(
        str(task.get("description", ""))
    )

    context = GateContext(
        stage="turn_start",
        sandbox=sandbox,
        deliverable=deliverable,
        required_keys=effective_keys,
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
    observed_hashes: Dict[str, str] = {}
    observed_stdout: List[str] = []
    verification_done = False
    # Why the arm failed, when it fails. Recorded so a run's log explains itself instead
    # of collapsing every failure into "budget exhausted".
    last_failure: Optional[str] = None

    for step in range(1, max_attempts + 1):
        attempt.steps = step
        call_started = time.perf_counter()
        try:
            reply = client.complete(history)
        except Exception as exc:  # noqa: BLE001 - a model failure ends the attempt
            error_kind = getattr(exc, "error_kind", "")
            if error_kind in ("transport", "http"):
                attempt.transport_dead = True
                last_failure = f"transport_error: {error_kind}"
            else:
                last_failure = f"model_error: {type(exc).__name__}"
            attempt.model_calls = max(
                attempt.model_calls,
                int(getattr(client, "call_count", start_calls + step)) - start_calls,
            )
            break
        call_elapsed = time.perf_counter() - call_started
        attempt.model_calls = max(
            0, int(getattr(client, "call_count", start_calls + step)) - start_calls
        )

        # Measure real call latency through the model_call timeout gate.
        if engine is not None:
            timeout_verdict = engine.run(
                GateContext(
                    stage="model_call",
                    sandbox=sandbox,
                    model_call_seconds=call_elapsed,
                )
            )
            if not timeout_verdict.allowed:
                attempt.gate_blocks.append(
                    f"model_call/{timeout_verdict.gate}: {timeout_verdict.reason}"
                )
                last_failure = "model_call_timeout"
                break

        history.append({"role": "assistant", "content": reply})

        answer = _extract_answer(reply)
        if answer is not None:
            if engine is not None:
                grounding = engine.run(
                    GateContext(
                        stage="pre_answer",
                        sandbox=sandbox,
                        deliverable=deliverable,
                        required_keys=effective_keys,
                        candidate_answer=answer,
                        observed_stdout="\n".join(observed_stdout),
                        verification_done=verification_done,
                    )
                )
                if not grounding.allowed:
                    attempt.gate_blocks.append(
                        f"pre_answer/{grounding.gate}: {grounding.reason}"
                    )
                    if step < max_attempts:
                        if "not directly observed" in grounding.reason or (
                            "no command stdout observed" in grounding.reason
                        ):
                            verification_done = True
                        history.append(
                            {
                                "role": "user",
                                "content": (
                                    f"Verification gate blocked your answer: "
                                    f"{grounding.reason}\n"
                                    "Run a shell or python3 command to compute and "
                                    "verify the exact value, then reply ANSWER: <json>."
                                ),
                            }
                        )
                        continue

            deliverable.write_text(answer)
            if engine is not None:
                stop_check = engine.run(
                    GateContext(
                        stage="stop",
                        sandbox=sandbox,
                        deliverable=deliverable,
                        required_keys=effective_keys,
                    )
                )
                if not stop_check.allowed and step < max_attempts:
                    attempt.gate_blocks.append(
                        f"stop/{stop_check.gate}: {stop_check.reason}"
                    )
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                f"Output validation blocked your answer: "
                                f"{stop_check.reason}\n"
                                "Fix the JSON shape and reply ANSWER: <json>."
                            ),
                        }
                    )
                    continue

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
                    "one shell command (plain line, not tool JSON), or ANSWER: <json>.",
                }
            )
            continue

        tool_context = GateContext(
            stage="pre_tool",
            sandbox=sandbox,
            command=command,
            reads_this_turn=reads_this_turn,
            last_files_read=tuple(last_files_read),
            observed_hashes=dict(observed_hashes),
            deliverable=deliverable,
            required_keys=effective_keys,
        )
        if engine is not None:
            verdict = engine.run(tool_context)
            if not verdict.allowed:
                attempt.gate_blocks.append(f"pre_tool/{verdict.gate}: {verdict.reason}")
                history.append(
                    {
                        "role": "user",
                        "content": (
                            f"Command blocked by {verdict.gate} gate: {verdict.reason}. "
                            "Reply with a valid command inside the sandbox, or ANSWER: <json>."
                        ),
                    }
                )
                continue
            if verdict.repair:
                attempt.repairs.append({"gate": verdict.gate, "action": verdict.repair})
                if not verdict.repair.startswith("re-read "):
                    command = verdict.repair

        result = _execute(command, sandbox)
        if result.get("stdout"):
            observed_stdout.append(str(result["stdout"]))
        _record_read_files(command, sandbox, last_files_read, observed_hashes)

        post_context = GateContext(stage="post_tool", sandbox=sandbox, command=command)
        if engine is not None:
            engine.run(post_context)

        if command.split() and command.split()[0] in ("cat", "head", "tail", "find"):
            reads_this_turn += 1
        else:
            reads_this_turn = 0

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
        deliverable=deliverable,
        required_keys=effective_keys,
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
        task,
        client,
        Path(sandbox_path),
        engine=None,
        arm="MINIMAL",
        budget=resolved,
        max_attempts=min(ARM_MAX_ATTEMPTS, max(1, resolved.max_steps)),
    )


def run_gated(
    task: Dict[str, Any], budget: Any, sandbox_path: str, client: Any = None
) -> Attempt:
    """Arm entrypoint: GATED. AF2 gate set injected. Identical otherwise."""
    resolved = budget if isinstance(budget, Budget) else Budget(**dict(budget))
    engine = GateEngine(gate_module.default_gates())
    return run_task(
        task,
        client,
        Path(sandbox_path),
        engine=engine,
        arm="GATED",
        budget=resolved,
        max_attempts=min(ARM_MAX_ATTEMPTS, max(1, resolved.max_steps)),
    )
