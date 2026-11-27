#!/usr/bin/env python3
# verdict-bench runner/stats.py
#
# Paired-comparison statistics for the head-to-head arms.
#
# The finding worth having is not "A scored higher than B". With 25 tasks that is noise.
# The finding is WHERE THE ARMS DISAGREE. This module answers that from raw attempt
# records, in code, with the statistics named on the output:
#
#   * McNemar exact test   - is the paired difference distinguishable from zero?
#   * discordant analysis  - which tasks do the arms split on, and how cleanly does the
#                            loser lose? A 0/3 failure is a signal; a 2/3 is a shrug.
#   * Wilson / Clopper-Pearson intervals - never a bootstrap of an all-success sample,
#                            which by construction cannot report below the point estimate.
#
# Zero model calls, zero LLM. Input is one results file per arm.
#
# Usage:
#   python runner/stats.py --world1 results/world1_A_B_results.jsonl \
#                          --af2 results/world1_AF2_results.jsonl \
#                          --out results/world1_comparison.md

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REQUIRED_FIELDS = ("arm", "task_id", "task_pass", "criteria_passed", "criteria_total")

# The void-and-rerun policy, in code. An attempt killed by the wall clock was
# not graded on its merits: the recorded World-1 attempts carry rc=124 (the
# `timeout` command's exit code) on rows that were nevertheless recorded as
# passes - 17 of AF2's 25, 4 of B's 17, 2 of A's 18. Those rows are voided
# before any comparison is quoted, and the void count is published beside it.
VOIDABLE_RC = (124,)
VOID_REASONS: Dict[int, str] = {
    124: "timeout: killed by the wall clock - the attempt was not graded on its merits",
}
TRANSPORT_VOID_REASON = (
    "transport: upstream/relay unreachable with no model response - "
    "the attempt was not graded on its merits"
)


def _is_transport_dead_empty(record: Dict[str, Any]) -> bool:
    """An empty attempt caused by a transport/upstream outage (no model response)."""
    empty = record.get("answer_empty")
    if empty is None:
        empty = not bool(record.get("answer"))
    if not empty:
        return False
    if record.get("transport_dead") is True:
        return True
    stopped = str(record.get("stopped_reason", "")).lower()
    return stopped.startswith("transport_error")


def is_void(record: Dict[str, Any]) -> bool:
    """One predicate, one policy: wall-clock timeouts or transport-dead empties."""
    return record.get("rc") in VOIDABLE_RC or _is_transport_dead_empty(record)


def void_reason(record: Dict[str, Any]) -> str:
    rc = record.get("rc")
    if isinstance(rc, int) and rc in VOID_REASONS:
        return VOID_REASONS[rc]
    if _is_transport_dead_empty(record):
        stopped = str(record.get("stopped_reason", "")).strip()
        return (
            f"{TRANSPORT_VOID_REASON} ({stopped})"
            if stopped
            else TRANSPORT_VOID_REASON
        )
    return f"voided: rc={rc}"


class StatsError(RuntimeError):
    """Raised when the attempt records cannot support the statistic being asked for."""


# ------------------------------------------------------------------ record loading


@dataclass(frozen=True)
class Attempt:
    """One recorded attempt: a graded outcome, not a delivery."""

    arm: str
    task_id: str
    family: str
    task_pass: bool
    criteria_passed: int
    criteria_total: int


def load_attempts(path: Path, void_timeouts: bool = True) -> List[Attempt]:
    """Read one JSONL results file. Rejects malformed records loudly.

    Voided attempts (rc=124) are EXCLUDED by default and reported separately by
    load_voids - quoting a comparison that counts a killed attempt's pass is
    the World-1 headline's defect, and the policy is code, not a footnote.
    """
    attempts: List[Attempt] = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StatsError(f"{path}:{line_no}: not valid JSON: {exc}") from exc
        missing = [f for f in REQUIRED_FIELDS if f not in record]
        if missing:
            raise StatsError(f"{path}:{line_no}: missing field(s) {', '.join(missing)}")
        if void_timeouts and is_void(record):
            continue
        attempts.append(
            Attempt(
                arm=record["arm"],
                task_id=record["task_id"],
                family=record.get("family", "unknown"),
                task_pass=bool(record["task_pass"]),
                criteria_passed=int(record["criteria_passed"]),
                criteria_total=int(record["criteria_total"]),
            )
        )
    if not attempts:
        raise StatsError(f"{path}: contains no attempt records")
    return attempts


def load_voids(path: Path) -> List[Dict[str, Any]]:
    """The voided records, with the reason each was voided."""
    voids: List[Dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if is_void(record):
            voids.append(
                {
                    "arm": record.get("arm"),
                    "task_id": record.get("task_id"),
                    "rc": record.get("rc"),
                    "task_pass": record.get("task_pass"),
                    "wall_s": record.get("wall_s"),
                    "reason": void_reason(record),
                }
            )
    return voids


def index_by_task(attempts: Iterable[Attempt]) -> Dict[str, Attempt]:
    return {a.task_id: a for a in attempts}


# ------------------------------------------------------------------- exact tests


def binomial_exact_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial p-value for k successes of n trials at p=0.5.

    Sums the probabilities of every outcome at least as extreme as the observed one.
    """
    if n == 0:
        raise StatsError("binomial exact test needs n > 0")
    if not 0 <= k <= n:
        raise StatsError(f"k={k} outside 0..n={n}")
    observed = math.comb(n, k)
    tail = sum(math.comb(n, i) for i in range(n + 1) if math.comb(n, i) <= observed)
    return min(1.0, tail / 2**n)


@dataclass
class McNemarResult:
    """Paired binary comparison. discordant pairs carry all the information."""

    a_only: int
    b_only: int
    both_pass: int
    both_fail: int
    p_value: float

    @property
    def discordant(self) -> int:
        return self.a_only + self.b_only


def mcnemar(
    a: Sequence[Attempt], b: Sequence[Attempt], task_ids: Optional[Sequence[str]] = None
) -> McNemarResult:
    """Exact McNemar test on tasks both arms attempted."""
    if len(a) != len(b):
        raise StatsError(
            "arms attempted different task counts; pair them before comparing "
            f"({len(a)} vs {len(b)})"
        )
    a_map = index_by_task(a)
    b_map = index_by_task(b)
    shared = sorted(set(a_map) & set(b_map))
    if task_ids is not None:
        missing = [t for t in task_ids if t not in shared]
        if missing:
            raise StatsError(
                f"requested tasks absent from one arm: {', '.join(missing)}"
            )
        shared = sorted(task_ids)
    if not shared:
        raise StatsError("no tasks attempted by both arms")

    a_only = sum(1 for t in shared if a_map[t].task_pass and not b_map[t].task_pass)
    b_only = sum(1 for t in shared if b_map[t].task_pass and not a_map[t].task_pass)
    both = sum(1 for t in shared if a_map[t].task_pass and b_map[t].task_pass)
    neither = sum(
        1 for t in shared if not a_map[t].task_pass and not b_map[t].task_pass
    )
    discordant = a_only + b_only
    # Zero discordant pairs is not a degenerate test: it is PERFECT agreement,
    # and the honest p-value is 1.0 (no difference detectable). Raising here
    # would fail exactly the runs that agree completely.
    if discordant == 0:
        return McNemarResult(
            a_only=a_only,
            b_only=b_only,
            both_pass=both,
            both_fail=neither,
            p_value=1.0,
        )
    return McNemarResult(
        a_only=a_only,
        b_only=b_only,
        both_pass=both,
        both_fail=neither,
        p_value=binomial_exact_two_sided(a_only, discordant),
    )


# --------------------------------------------------------------- interval bounds


def wilson_lower_bound(successes: int, n: int, confidence: float = 0.95) -> float:
    """Wilson score interval lower bound.

    Preferred over a bootstrap here: resampling an all-success sample can never produce a
    bound below the point estimate, which is how 25/25 gets reported as "100%-100%".
    """
    if n <= 0:
        raise StatsError("Wilson interval needs n > 0")
    if not 0 <= successes <= n:
        raise StatsError(f"successes={successes} outside 0..n={n}")
    if not 0.0 < confidence < 1.0:
        raise StatsError(f"confidence must be in (0,1), got {confidence}")

    z = _normal_quantile(1.0 - (1.0 - confidence) / 2.0)
    p = successes / n
    denominator = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - margin) / denominator)


def _normal_quantile(p: float) -> float:
    """Inverse standard-normal CDF (Acklam's rational approximation)."""
    if not 0.0 < p < 1.0:
        raise StatsError(f"quantile argument must be in (0,1), got {p}")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low, p_high = 0.02425, 1.0 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p > p_high:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(
            ((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]
        ) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
    )


# ------------------------------------------------------- disagreement analysis


@dataclass(frozen=True)
class DiscordantTask:
    task_id: str
    family: str
    winner: str
    loser: str
    winner_criteria: str
    loser_criteria: str
    loser_scored_zero: bool


def discordant_tasks(
    a: Sequence[Attempt],
    b: Sequence[Attempt],
    a_name: str = "A",
    b_name: str = "B",
    task_ids: Optional[Sequence[str]] = None,
) -> List[DiscordantTask]:
    """Tasks the arms split on, with how cleanly the loser lost."""
    a_map = index_by_task(a)
    b_map = index_by_task(b)
    shared = sorted(set(a_map) & set(b_map))
    if task_ids is not None:
        shared = sorted(t for t in task_ids if t in shared)

    rows: List[DiscordantTask] = []
    for t in shared:
        ra, rb = a_map[t], b_map[t]
        if ra.task_pass == rb.task_pass:
            continue
        if ra.task_pass:
            winner, loser, wr, lr = ra, rb, a_name, b_name
        else:
            winner, loser, wr, lr = rb, ra, b_name, a_name
        rows.append(
            DiscordantTask(
                task_id=t,
                family=winner.family,
                winner=wr,
                loser=lr,
                winner_criteria=f"{winner.criteria_passed}/{winner.criteria_total}",
                loser_criteria=f"{loser.criteria_passed}/{loser.criteria_total}",
                loser_scored_zero=loser.criteria_passed == 0,
            )
        )
    return rows


def family_attribution(
    rows: Sequence[DiscordantTask],
) -> List[Tuple[str, str, int, int]]:
    """(family, winning arm, wins, losses) per family, sorted by disagreement volume.

    Reported as counts, never as a property of the arm: 9 discordant tasks in one world
    is a hypothesis, not a durable characteristic.
    """
    tally: Dict[Tuple[str, str], List[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        tally[(row.family, row.winner)][0] += 1
        tally[(row.family, row.loser)][1] += 1
    ordered = sorted(tally.items(), key=lambda kv: (-(kv[1][0] + kv[1][1]), kv[0]))
    return [(family, arm, counts[0], counts[1]) for (family, arm), counts in ordered]


# ------------------------------------------------------------------- reporting


def paired_failure_reasons(
    a: Sequence[Attempt], b: Sequence[Attempt], a_name: str, b_name: str
) -> List[str]:
    """Why these two arms cannot be paired, if they cannot. Empty list means they can."""
    reasons: List[str] = []
    if len(a) != len(b):
        reasons.append(f"{a_name} scored {len(a)} tasks but {b_name} scored {len(b)}")
    a_map = index_by_task(a)
    b_map = index_by_task(b)
    only_a = sorted(set(a_map) - set(b_map))
    only_b = sorted(set(b_map) - set(a_map))
    if only_a:
        reasons.append(f"tasks only {a_name} attempted: {', '.join(only_a)}")
    if only_b:
        reasons.append(f"tasks only {b_name} attempted: {', '.join(only_b)}")
    dupes_a = sorted(
        {t for t in (x.task_id for x in a) if sum(1 for y in a if y.task_id == t) > 1}
    )
    if dupes_a:
        reasons.append(f"{a_name} has repeated tasks: {', '.join(dupes_a)}")
    dupes_b = sorted(
        {t for t in (x.task_id for x in b) if sum(1 for y in b if y.task_id == t) > 1}
    )
    if dupes_b:
        reasons.append(f"{b_name} has repeated tasks: {', '.join(dupes_b)}")
    return reasons


def summarise_arm(name: str, attempts: Sequence[Attempt]) -> Dict[str, Any]:
    scored = len(attempts)
    passed = sum(1 for a in attempts if a.task_pass)
    crit_pass = sum(a.criteria_passed for a in attempts)
    crit_total = sum(a.criteria_total for a in attempts)
    return {
        "arm": name,
        "tasks_scored": scored,
        "tasks_passed": passed,
        "pass_at_1": passed / scored if scored else 0.0,
        "pass_at_1_lower_95": wilson_lower_bound(passed, scored) if scored else 0.0,
        "criteria_passed": crit_pass,
        "criteria_total": crit_total,
        "criteria_rate": crit_pass / crit_total if crit_total else 0.0,
    }


def render_report(
    a_name: str,
    attempts_a: Sequence[Attempt],
    b_name: str,
    attempts_b: Sequence[Attempt],
    extra_arms: Optional[Dict[str, Sequence[Attempt]]] = None,
) -> str:
    """Markdown comparison: summary, McNemar, and the disagreement that matters."""
    summary_a = summarise_arm(a_name, attempts_a)
    summary_b = summarise_arm(b_name, attempts_b)
    test = mcnemar(attempts_a, attempts_b)
    rows = discordant_tasks(attempts_a, attempts_b, a_name, b_name)

    lines: List[str] = []
    lines.append("# World 1 — paired arm comparison")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Arm | Tasks | Pass@1 | 95% lower bound | Criteria |")
    lines.append("|-----|-------|--------|-----------------|----------|")
    lines.append(
        f"| {a_name} | {summary_a['tasks_passed']}/{summary_a['tasks_scored']} "
        f"| {summary_a['pass_at_1']:.0%} "
        f"| {summary_a['pass_at_1_lower_95']:.1%} "
        f"| {summary_a['criteria_passed']}/{summary_a['criteria_total']} |"
    )
    lines.append(
        f"| {b_name} | {summary_b['tasks_passed']}/{summary_b['tasks_scored']} "
        f"| {summary_b['pass_at_1']:.0%} "
        f"| {summary_b['pass_at_1_lower_95']:.1%} "
        f"| {summary_b['criteria_passed']}/{summary_b['criteria_total']} |"
    )
    if extra_arms:
        for name, attempts in extra_arms.items():
            s = summarise_arm(name, attempts)
            lines.append(
                f"| {name} | {s['tasks_passed']}/{s['tasks_scored']} "
                f"| {s['pass_at_1']:.0%} "
                f"| {s['pass_at_1_lower_95']:.1%} "
                f"| {s['criteria_passed']}/{s['criteria_total']} |"
            )
    lines.append("")

    lines.append(f"## {a_name} vs {b_name} — McNemar exact test")
    lines.append("")
    lines.append("| Outcome | Count |")
    lines.append("|---------|-------|")
    lines.append(f"| {a_name} passes, {b_name} fails | {test.a_only} |")
    lines.append(f"| {b_name} passes, {a_name} fails | {test.b_only} |")
    lines.append(f"| both pass | {test.both_pass} |")
    lines.append(f"| both fail | {test.both_fail} |")
    lines.append("")
    lines.append(
        f"Discordant pairs: **{test.discordant}** — exact two-sided p = **{test.p_value:.4f}**."
    )
    if test.p_value >= 0.05:
        lines.append(
            "No detectable difference between the arms. The overall gap is "
            "**indistinguishable at this sample size**."
        )
    else:
        lines.append("Difference detected at alpha=0.05.")
    lines.append("")

    lines.append("## Where they disagree")
    lines.append("")
    if not rows:
        lines.append("The arms passed and failed on identical tasks.")
    else:
        clean = sum(1 for r in rows if r.loser_scored_zero)
        lines.append(
            "| Task | Family | Winner | Loser | Winner criteria | Loser criteria |"
        )
        lines.append(
            "|------|--------|--------|-------|-----------------|----------------|"
        )
        for r in rows:
            lines.append(
                f"| {r.task_id} | {r.family} | {r.winner} | {r.loser} "
                f"| {r.winner_criteria} | {r.loser_criteria} |"
            )
        lines.append("")
        lines.append(
            f"**{len(rows)} of {summary_a['tasks_scored']} tasks split**, "
            f"and on **{clean} of them the losing arm scored zero criteria** — "
            "a clean total failure, not partial credit."
        )
        lines.append("")
        lines.append("### Family attribution (counts, not conclusions)")
        lines.append("")
        lines.append("| Family | Arm wins | That arm also loses |")
        lines.append("|--------|----------|---------------------|")
        for family, arm, wins, losses in family_attribution(rows):
            lines.append(f"| {family} | {arm} ({wins}) | {losses} |")
        lines.append("")
        lines.append(
            "These are 9 discordant tasks from a single world. They are a "
            "hypothesis about where the arms differ, not an established "
            "property of either harness."
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "Generated by `runner/stats.py`. No model calls; every number above is "
        "computed from the recorded attempt records in code."
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="verdict-bench paired arm statistics")
    parser.add_argument(
        "--attempts",
        nargs="+",
        required=True,
        help="attempts files; each may hold several arms (arm field)",
    )
    parser.add_argument(
        "--compare",
        nargs=2,
        required=True,
        metavar=("A", "B"),
        help="the two arms to pair and test",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=[],
        metavar="ARM",
        help="additional arms to list in the summary table",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="write the markdown report here"
    )
    args = parser.parse_args()

    try:
        all_attempts: List[Attempt] = []
        for path in args.attempts:
            all_attempts.extend(load_attempts(Path(path)))
    except (StatsError, OSError) as exc:
        print(f"STATS INVALID: {exc}", file=sys.stderr)
        return 2

    by_arm: Dict[str, List[Attempt]] = defaultdict(list)
    for attempt in all_attempts:
        by_arm[attempt.arm].append(attempt)

    a_name, b_name = args.compare
    for needed in (a_name, b_name, *args.include):
        if needed not in by_arm:
            print(
                f"STATS INVALID: arm {needed!r} not in " f"{sorted(by_arm)}",
                file=sys.stderr,
            )
            return 2

    failure_reasons = paired_failure_reasons(
        by_arm[a_name], by_arm[b_name], a_name, b_name
    )
    if failure_reasons:
        print(
            "STATS INVALID: arms cannot be paired — " + "; ".join(failure_reasons),
            file=sys.stderr,
        )
        return 2

    report = render_report(
        a_name,
        by_arm[a_name],
        b_name,
        by_arm[b_name],
        {n: by_arm[n] for n in args.include} or None,
    )
    print(report)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report)
        print(f"\nwritten: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
