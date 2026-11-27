#!/usr/bin/env python3
# verdict-bench harness/adversary.py
#
# The contradiction injector - the machinery that makes "recovers when wrong"
# a MEASURED claim instead of an anecdote.
#
# Concept from glass-box-alpha/devils_advocate; deliberately NOT its form. That
# is an LLM agent that argues, and an LLM adversary is unmeasurable - it changes
# with the model's mood, so "it recovered" would describe the adversary as much
# as the arm. This is code: fixed seed, fixed contradiction, fires once per task
# at a seeded attempt, records itself as harness-authored.
#
# The contradiction is DETECTABLE by construction: the injected text names the
# earlier reading AND the new one, and says to reconcile. What gets measured
# (D.4) is what the arm does next - revises, or plows on with the stale value.

from __future__ import annotations

from typing import Any, Dict, Optional


def shift_digits(text: str, seed: int) -> str:
    """The contradiction: every digit advanced by a seed-derived shift.

    Deterministic (same seed, same input, same output, forever), obviously
    different from the original, and independent of any world's rules - it
    cannot leak an answer, only displace the evidence the arm already had. The
    seed enters the shift so two seeds displace differently: a seed that
    produced the same contradiction every time would be decoration.
    """
    shift = (seed % 9) + 1
    return "".join(str((int(ch) + shift) % 10) if ch.isdigit() else ch for ch in text)


class Adversary:
    """Injects one contradiction per task, at a seeded attempt, in code."""

    def __init__(self, seed: int, at_attempt: int = 1):
        self.seed = seed
        self.at_attempt = at_attempt
        self.injected: Dict[str, Dict[str, Any]] = {}

    def maybe_inject(
        self,
        task_id: str,
        attempt: int,
        cmd: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Rewrite a command result to contradict the arm's earlier reading.

        Fires once per task, at the seeded attempt, only on a command that
        actually produced output to contradict. Returns the result - rewritten
        or untouched - and records the injection under the task id.
        """
        if task_id in self.injected or attempt != self.at_attempt:
            return result
        stdout = (result.get("stdout") or "").strip()
        if not result.get("ok") or not stdout:
            return result

        original = stdout.splitlines()[0]
        contradicted = shift_digits(original, self.seed)
        rewritten = dict(result)
        rewritten["stdout"] = (
            "CONTRADICTION INJECTED (benchmark adversary, "
            f"seed {self.seed}):\n"
            f"  earlier reading: {original}\n"
            f"  the file now reads: {contradicted}\n"
            "  Reconcile before answering.\n\n"
            f"{result.get('stdout', '')}"
        )
        rewritten["adversary_injected"] = True
        self.injected[task_id] = {
            "seed": self.seed,
            "attempt": attempt,
            "cmd": cmd,
            "original": original,
            "contradicted": contradicted,
        }
        return rewritten

    def event_for(self, task_id: str) -> Optional[Dict[str, Any]]:
        """The harness-authored record for the rows, or None."""
        return self.injected.get(task_id)

    def detail(self, task_id: str) -> str:
        """One line for the hypothesis log, so the audit can tell a harness
        injection from a model correction at a glance."""
        record = self.injected.get(task_id)
        if record is None:
            return ""
        return (
            f"contradiction injected on {record['cmd']!r}, attempt "
            f"{record['attempt'] + 1}: {record['original']!r} -> "
            f"{record['contradicted']!r} (seed {record['seed']})"
        )
