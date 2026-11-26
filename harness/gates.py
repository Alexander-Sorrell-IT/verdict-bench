#!/usr/bin/env python3
# verdict-bench harness/gates.py
#
# The AF2 gates, ported from CLI-Enforcement's architecture.
#
# Two things are borrowed from cli-enforcement (Alexander-Sorrell-IT/CLI-Enforcement,
# src/cli_enforcement/deploy.py and engine/):
#
#   1. CANONICAL STAGES, platform-neutral. Gates are written against a stage
#      (pre_tool, post_tool, turn_start, stop), never against one platform's event
#      names; STAGE_PATTERNS maps a platform's real events onto them.
#   2. NO WARNINGS, ONLY BLOCK OR ALLOW. "ABSOLUTE ENFORCEMENT" in enforce_check.py.
#      A gate that warns is a gate the model learns to ignore; in a benchmark a
#      warned-but-allowed failure is how a broken run gets recorded as a pass.
#
# And one pattern from bash_gate.py: REPAIR rather than refuse when the fix is
# unambiguous, returning the safe action and recording the repair.
#
# The gates themselves are verdict-bench's AF2 set: completion, output validation,
# read-streak, model-call timeout, anti-hallucination, sandbox confinement.

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# Canonical stages, platform-neutral. Canonical event names are the familiar
# Claude Code ones; STAGE_PATTERNS finds the equivalent on any other platform.
STAGE_PATTERNS: Dict[str, str] = {
    "turn_start": r"session.*start|^start|turn.*start",
    "user_prompt": r"prompt|before.*agent|user.*submit",
    "pre_tool": r"(pre|before).?tool(?!.*select)",
    "post_tool": r"(post|after).?tool",
    "tool_failure": r"tool.*fail",
    "model_call": r"(pre|before).?(model|llm|inference)",
    "stop": r"^stop$|after.*agent|turn.*end",
}


def map_stages_to_events(events: Sequence[str]) -> Tuple[Dict[str, str], List[str]]:
    """Match each canonical stage to one of a platform's real event names.

    Returns ({stage: event_name}, [stages with no match]). An unmatched stage is
    reported rather than silently dropped: a gate that never fires is worse than
    no gate, because the run still believes it ran.
    """
    mapping: Dict[str, str] = {}
    unmatched: List[str] = []
    for stage, pattern in STAGE_PATTERNS.items():
        compiled = re.compile(pattern, re.IGNORECASE)
        hit = next((e for e in events if compiled.search(e)), None)
        if hit:
            mapping[stage] = hit
        else:
            unmatched.append(stage)
    return mapping, unmatched


@dataclass
class Verdict:
    """One gate's decision. `blocked` is the only negative outcome - there is no warn."""

    allowed: bool
    reason: str = ""
    repair: Optional[str] = None
    gate: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - clarity at call sites
        return self.allowed


ALLOW = Verdict(allowed=True)


@dataclass
class GateContext:
    """Everything a gate may look at. Immutable by convention, never by force."""

    stage: str
    sandbox: Path
    deliverable: Optional[Path] = None
    required_keys: Tuple[str, ...] = ()
    command: Optional[str] = None
    reads_this_turn: int = 0
    last_files_read: Tuple[str, ...] = ()
    files_claimed_read: Tuple[str, ...] = ()
    observed_hashes: Dict[str, str] = field(default_factory=dict)
    wall_seconds: float = 0.0
    model_call_seconds: float = 0.0


class Gate:
    """One blocking check at one stage."""

    name = "gate"
    stage = "pre_tool"

    def check(self, context: GateContext) -> Verdict:  # pragma: no cover - interface
        raise NotImplementedError


# ------------------------------------------------------------------- the gate set


class CompletionGate(Gate):
    """The run may not end until the deliverable exists.

    This is the AF2 gate that took OmniCLI from 72% to a clean pass rate, and it is the
    one whose absence manufactured the 25/25: a placeholder string satisfies
    "non-empty and parses", but it does not satisfy "the file the task asked for exists".
    """

    name = "completion"
    stage = "stop"

    def check(self, context: GateContext) -> Verdict:
        if context.deliverable is None:
            return Verdict(
                False, f"{self.name}: no deliverable declared", gate=self.name
            )
        if not context.deliverable.exists():
            return Verdict(
                False,
                f"{self.name}: deliverable {context.deliverable} does not exist",
                gate=self.name,
            )
        if context.deliverable.stat().st_size == 0:
            return Verdict(False, f"{self.name}: deliverable is empty", gate=self.name)
        return Verdict(True, gate=self.name)


class OutputValidationGate(Gate):
    """The deliverable must parse and carry the keys the task requires."""

    name = "output_validation"
    stage = "stop"

    def check(self, context: GateContext) -> Verdict:
        if context.deliverable is None or not context.deliverable.exists():
            return Verdict(False, f"{self.name}: nothing to validate", gate=self.name)
        text = context.deliverable.read_text(errors="replace").strip()
        if not text:
            return Verdict(False, f"{self.name}: deliverable empty", gate=self.name)

        import json

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return Verdict(
                False, f"{self.name}: deliverable is not valid JSON", gate=self.name
            )

        if context.required_keys and isinstance(parsed, dict):
            missing = [k for k in context.required_keys if k not in parsed]
            if missing:
                return Verdict(
                    False,
                    f"{self.name}: missing required keys {missing}",
                    gate=self.name,
                )
        return Verdict(True, gate=self.name)


class ReadStreakGate(Gate):
    """After N consecutive single-file reads, force one script instead.

    Prevents an arm burning its whole budget rereading one file at a time.
    """

    name = "read_streak"
    stage = "pre_tool"
    max_streak = 4

    def check(self, context: GateContext) -> Verdict:
        if context.reads_this_turn < self.max_streak:
            return Verdict(True, gate=self.name)
        return Verdict(
            False,
            f"{self.name}: {context.reads_this_turn} consecutive single-file reads; "
            f"consolidate into one scripted command (limit {self.max_streak})",
            gate=self.name,
        )


class ModelCallTimeoutGate(Gate):
    """A single model call may not run past the deadline.

    Measured, not assumed: the arm cannot report success while a call is still hanging,
    which is the quiet failure that produced the 0.213s "25/25" run's opposite twin.
    """

    name = "model_call_timeout"
    stage = "model_call"
    max_seconds = 600.0

    def check(self, context: GateContext) -> Verdict:
        if context.model_call_seconds <= self.max_seconds:
            return Verdict(True, gate=self.name)
        return Verdict(
            False,
            f"{self.name}: model call ran {context.model_call_seconds:.1f}s "
            f"(limit {self.max_seconds:.0f}s)",
            gate=self.name,
        )


class AntiHallucinationGate(Gate):
    """An arm may not claim to have read a file it did not read, or rely on a stale read.

    Ported from enforce_check.py check #8 - BLOCK if the file was not read, the hash
    mismatched, or the read went stale. In a benchmark this is what stops an arm
    asserting a value it never observed, which is exactly how a fabricated answer looks.
    """

    name = "anti_hallucination"
    stage = "pre_tool"

    def check(self, context: GateContext) -> Verdict:
        for claimed in context.files_claimed_read:
            if claimed not in context.last_files_read:
                return Verdict(
                    False,
                    f"{self.name}: {claimed} was never read this turn; "
                    "read it or drop the claim",
                    gate=self.name,
                )
        for path, observed in context.observed_hashes.items():
            current = _file_hash(Path(path))
            if current is not None and current != observed:
                # The repair is deterministic - so return it, and let the engine record it.
                return Verdict(
                    True,
                    f"{self.name}: {path} changed since it was read",
                    repair=f"re-read {path}",
                    gate=self.name,
                )
        return Verdict(True, gate=self.name)


class SandboxGate(Gate):
    """Commands and paths stay inside the arm's sandbox.

    Generalised from bash_gate.py's self-protection: rather than only protecting the
    enforcement files, protect everything the arm was not given.
    """

    name = "sandbox"
    stage = "pre_tool"

    def check(self, context: GateContext) -> Verdict:
        if not context.command:
            return Verdict(True, gate=self.name)
        command = context.command

        for raw in re.findall(r"[\w./~-]+", command):
            candidate = raw.lstrip("-")
            if candidate.startswith(("/", "~/")) or candidate.startswith(".."):
                resolved = Path(candidate).expanduser()
                if not _inside(resolved, context.sandbox):
                    return Verdict(
                        False,
                        f"{self.name}: command reaches outside the sandbox: {raw}",
                        gate=self.name,
                    )

        destructive = [
            f for f in ("--force", "-f", "--hard", "-rf") if f in command.split()
        ]
        if destructive:
            return Verdict(
                True,
                " ".join([f"{self.name}: stripped destructive flag(s) {destructive}"]),
                repair=" ".join(
                    token for token in command.split() if token not in destructive
                ),
                gate=self.name,
            )
        return Verdict(True, gate=self.name)


def _inside(candidate: Path, sandbox: Path) -> bool:
    try:
        candidate.resolve().relative_to(sandbox.resolve())
        return True
    except (ValueError, OSError):
        return False


def _file_hash(path: Path) -> Optional[str]:
    import hashlib

    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ the engine


class GateEngine:
    """Runs the gates registered for a stage. BLOCK stops the action; repairs are returned."""

    def __init__(self, gates: Optional[Sequence[Gate]] = None) -> None:
        self.gates: List[Gate] = list(gates) if gates is not None else default_gates()
        self.repairs: List[Dict[str, str]] = []

    def run(self, context: GateContext) -> Verdict:
        """Run every gate for the stage. First BLOCK wins; repairs are recorded."""
        for gate in self.gates:
            if gate.stage != context.stage:
                continue
            verdict = gate.check(context)
            if verdict.repair and verdict.allowed:
                self.repairs.append(
                    {
                        "gate": gate.name,
                        "stage": context.stage,
                        "action": verdict.repair,
                        "reason": verdict.reason,
                    }
                )
            if not verdict.allowed:
                return verdict
        return ALLOW


def default_gates() -> List[Gate]:
    """The AF2 set. Order matters only for which block is reported first."""
    return [
        CompletionGate(),
        OutputValidationGate(),
        ReadStreakGate(),
        ModelCallTimeoutGate(),
        AntiHallucinationGate(),
        SandboxGate(),
    ]
