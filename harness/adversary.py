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
        original_value = original.rsplit(None, 1)[-1] if original else ""
        contradicted_value = contradicted.rsplit(None, 1)[-1] if contradicted else ""
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
            "original_value": original_value,
            "contradicted_value": contradicted_value,
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


def measure_recovery(cycle: Any, event: Dict[str, Any]) -> Dict[str, Any]:
    """D.4 - the measured recovery counter, replacing "resolved within <=3 tests".

    Fixed before any graded result exists, so it cannot be tuned to flatter a
    run. After a contradiction was injected, did the arm revise? Two signals,
    both from committed evidence:

      engaged - the arm made its final decision at or after the injection
                (resolved_at_attempt), or ran further commands after it
      answer_outcome - reverted-to-the-evidence (the answer carries the TRUE
                value, not the displaced one) / took-the-injection (it answered
                the contradicted value) / other / no-answer

    recovered = engaged AND reverted.

    The engagement bar is NOT "ran another command": a live run showed the arm
    reading the contradiction and committing the true value in the same
    decision, which is recovery, not caution. What is NOT recovery: committing
    before the contradiction ever appeared (the control's outcome, not a
    response), taking the displaced value, or returning nothing.
    """
    injected_index = next(
        (
            i
            for i, test in enumerate(cycle.tests)
            if test["result"].get("adversary_injected")
        ),
        None,
    )
    injected_attempt = event.get("attempt")
    resolved_at = getattr(cycle, "resolved_at_attempt", None)
    investigated = injected_index is not None and injected_index + 1 < len(cycle.tests)
    decided_after = (
        injected_attempt is not None
        and resolved_at is not None
        and resolved_at >= injected_attempt
    )
    engaged = investigated or decided_after
    answer = (cycle.final_answer or "").strip()
    if not answer:
        outcome = "no-answer"
    elif event["contradicted_value"] in answer:
        outcome = "took-the-injection"
    elif event["original_value"] in answer:
        outcome = "reverted-to-the-evidence"
    else:
        outcome = "other"
    return {
        "engaged": engaged,
        "investigated_further": investigated,
        "decided_after_contradiction": decided_after,
        "answer_outcome": outcome,
        "recovered": engaged and outcome == "reverted-to-the-evidence",
    }
