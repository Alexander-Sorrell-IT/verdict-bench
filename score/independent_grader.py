#!/usr/bin/env python3
# verdict-bench score/independent_grader.py
#
# The sealed grader. It produces the verdict, and NOTHING else may.
#
# ISOLATION - this module imports no harness/ code. The arms live in harness/;
# a grader that shares code with an arm grades its own homework. It reads the
# run's rows (answers included), the withheld gold (as an ARGUMENT), and the
# frozen split. runner/ is the spine both sides already stand on - stats for
# the comparison math, receipts for the chain.
#
# INDEPENDENCE - it does not trust the run's verdicts. Every row is re-graded
# from its answer against gold (world/grader.py's sealed, gold-only signature),
# and a row whose re-grade disagrees with the recorded verdict is a fatal
# finding: a run whose grading lied is not an inconclusive run, it is a
# rejected one. The World-1 attempts recorded task_pass with no gold anywhere
# on disk - that is exactly the hole this closes.
#
# RULES - fixed here, before any result exists (C.2). Not tuned per run;
# changing them is a commit, and RULE_VERSION rides every receipt:
#   PASS         - pass@1 AND its 95% Wilson lower bound both clear the target
#   INCONCLUSIVE - the estimate clears but the bound does not, or the arm
#                  returned an empty answer on more than EMPTY_RATE_CEILING of
#                  its live tasks (answered too little to score != scored badly)
#   REJECT       - otherwise, or a reconciliation failure
#
# The run's verdict is the weakest arm's: one REJECT invalidates the comparison.

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR / "runner"))
sys.path.insert(0, str(BENCH_DIR))

from receipts import (  # noqa: E402
    ReceiptChain,
    receipt_run_manifest,
    receipt_task_grade,
    verify_receipt_chain,
)
from stats import (  # noqa: E402
    StatsError,
    index_by_task,
    is_void,
    load_attempts,
    mcnemar,
    summarise_arm,
)

RULE_VERSION = "1"
EMPTY_RATE_CEILING = 0.20


def load_rows(rows_path: Path) -> List[Dict[str, Any]]:
    """One JSON object per line; a malformed line is fatal, not skipped."""
    rows: List[Dict[str, Any]] = []
    for line_no, line in enumerate(rows_path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise StatsError(f"{rows_path}:{line_no}: not valid JSON: {exc}") from exc
    return rows


def reconcile(rows: Sequence[Dict[str, Any]], gold: Dict[str, Any]) -> List[str]:
    """Re-grade every row from its answer against gold. Returns the discrepancies.

    `gold` is the FULL gold document (with its "answers" mapping) - the sealed
    grader's signature, exactly as run_world and bench pass it.
    """
    from world.grader import grade_task_output

    discrepancies: List[str] = []
    for row in rows:
        graded = grade_task_output(row["task_id"], row.get("answer", ""), gold)
        if bool(graded["passed"]) != bool(row["task_pass"]):
            discrepancies.append(
                f"{row['arm']}/{row['task_id']}: recorded task_pass="
                f"{row['task_pass']} but the independent grade says "
                f"{graded['passed']} ({graded.get('reason', '')})"
            )
    return discrepancies


def _empty_rate(rows: Sequence[Dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if r.get("answer_empty", not r.get("answer"))) / len(
        rows
    )


def _split_map(split_path: Optional[Path]) -> Dict[str, str]:
    """Load task_id -> 'dev' | 'test' from a frozen split file, if supplied."""
    if split_path is None or not Path(split_path).is_file():
        return {}
    doc = json.loads(Path(split_path).read_text())
    mapping: Dict[str, str] = {}
    for split_name in ("dev", "test"):
        for entry in doc.get(split_name, []):
            task_id = entry.get("task_id")
            if task_id:
                mapping[str(task_id)] = split_name
    return mapping


def _summarise_splits(
    arm_name: str,
    attempts: Sequence[Any],
    live_rows: Sequence[Dict[str, Any]],
    split_map: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    """Compute dev vs. held-out test breakdown for one arm's live attempts."""
    row_splits: Dict[str, str] = {}
    for row in live_rows:
        task_id = str(row.get("task_id", ""))
        raw_split = str(row.get("split", "")).strip().lower()
        if task_id and raw_split and raw_split != "unknown":
            row_splits[task_id] = raw_split

    buckets: Dict[str, List[Any]] = {}
    for attempt in attempts:
        split_name = split_map.get(attempt.task_id) or row_splits.get(
            attempt.task_id, "unknown"
        )
        buckets.setdefault(split_name, []).append(attempt)

    ordered_keys = [k for k in ("dev", "test") if k in buckets]
    ordered_keys.extend(sorted(k for k in buckets if k not in ("dev", "test")))

    breakdown: Dict[str, Dict[str, Any]] = {}
    for split_name in ordered_keys:
        summary = summarise_arm(f"{arm_name}:{split_name}", buckets[split_name])
        breakdown[split_name] = {
            "tasks_scored": summary["tasks_scored"],
            "tasks_passed": summary["tasks_passed"],
            "pass_at_1": summary["pass_at_1"],
            "pass_at_1_lower_95": summary["pass_at_1_lower_95"],
            "criteria_passed": summary["criteria_passed"],
            "criteria_total": summary["criteria_total"],
            "criteria_rate": summary["criteria_rate"],
        }
    return breakdown


def verdict_for(summary: Dict[str, Any], empty_rate: float, target: float) -> str:
    """The rules, fixed before results exist. See the module docstring."""
    if empty_rate > EMPTY_RATE_CEILING:
        return "INCONCLUSIVE"
    if summary["pass_at_1"] >= target and summary["pass_at_1_lower_95"] >= target:
        return "PASS"
    if summary["pass_at_1"] >= target:
        return "INCONCLUSIVE"
    return "REJECT"


def grade_run(
    rows_path: Path,
    gold_path: Path,
    target: float = 0.70,
    split_path: Optional[Path] = None,
    receipts_path: Optional[Path] = None,
    run_manifest: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Grade a run: reconcile, summarise, verdict, receipt. Returns the verdict."""
    gold = json.loads(Path(gold_path).read_text())
    rows = load_rows(Path(rows_path))
    if not rows:
        raise StatsError(f"no rows in {rows_path}")

    discrepancies = reconcile(rows, gold)
    attempts = load_attempts(Path(rows_path))
    by_arm: Dict[str, List[Any]] = {}
    for attempt in attempts:
        by_arm.setdefault(attempt.arm, []).append(attempt)

    splits = _split_map(split_path)
    per_arm: List[Dict[str, Any]] = []
    for arm_name in sorted(by_arm):
        arm_rows = [r for r in rows if r["arm"] == arm_name]
        live_arm_rows = [r for r in arm_rows if not is_void(r)]
        summary = summarise_arm(arm_name, by_arm[arm_name])
        summary["voided"] = len(arm_rows) - len(live_arm_rows)
        summary["empty_answer_rate"] = round(_empty_rate(live_arm_rows), 4)
        summary["splits"] = _summarise_splits(
            arm_name, by_arm[arm_name], live_arm_rows, splits
        )
        summary["verdict"] = verdict_for(summary, summary["empty_answer_rate"], target)
        per_arm.append(summary)

    verdicts = [s["verdict"] for s in per_arm]
    if discrepancies or "REJECT" in verdicts:
        run_verdict = "REJECT"
    elif all(v == "PASS" for v in verdicts):
        run_verdict = "PASS"
    else:
        run_verdict = "INCONCLUSIVE"

    comparison: Dict[str, Any] = {}
    if len(by_arm) >= 2:
        names = sorted(by_arm)
        # Pair on the tasks both arms attempted live - after voids the arms' live
        # task sets differ (World 1: A 22, B 13), and an unpaired McNemar is
        # not a comparison. The pairing count is disclosed.
        pairs: List[Dict[str, Any]] = []
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                a_map = index_by_task(by_arm[first])
                b_map = index_by_task(by_arm[second])
                shared = sorted(set(a_map) & set(b_map))
                if shared:
                    a_live = [a_map[task_id] for task_id in shared]
                    b_live = [b_map[task_id] for task_id in shared]
                    result = mcnemar(a_live, b_live)
                    pairs.append(
                        {
                            "arms": [first, second],
                            "paired_tasks": len(shared),
                            "a_only": result.a_only,
                            "b_only": result.b_only,
                            "both_pass": result.both_pass,
                            "both_fail": result.both_fail,
                            "discordant": result.discordant,
                            "p_value": result.p_value,
                        }
                    )
                else:
                    pairs.append(
                        {
                            "arms": [first, second],
                            "paired_tasks": 0,
                            "a_only": 0,
                            "b_only": 0,
                            "both_pass": 0,
                            "both_fail": 0,
                            "discordant": 0,
                            "p_value": 1.0,
                        }
                    )

        if len(names) == 2:
            primary = pairs[0]
            comparison = {
                "arms": names,
                "paired_tasks": primary["paired_tasks"],
                "a_only": primary["a_only"],
                "b_only": primary["b_only"],
                "both_pass": primary["both_pass"],
                "both_fail": primary["both_fail"],
                "discordant": primary["discordant"],
                "p_value": primary["p_value"],
                "pairs": pairs,
            }
        else:
            comparison = {
                "arms": names,
                "pairs": pairs,
            }

    split_hash = ""
    if split_path and Path(split_path).is_file():
        split_hash = hashlib.sha256(Path(split_path).read_bytes()).hexdigest()

    receipts: Dict[str, Any] = {"ok": None}
    if receipts_path is not None:
        chain = ReceiptChain(Path(receipts_path))
        receipt_run_manifest(
            chain,
            {
                "kind_detail": "independent_grade",
                "rule_version": RULE_VERSION,
                "rows": str(rows_path),
                "rows_sha256": hashlib.sha256(Path(rows_path).read_bytes()).hexdigest(),
                "gold": str(gold_path),
                "gold_sha256": hashlib.sha256(Path(gold_path).read_bytes()).hexdigest(),
                "split_sha256": split_hash,
                "target": str(target),
                **(run_manifest or {}),
            },
        )
        for row in rows:
            receipt_task_grade(
                chain,
                row["arm"],
                row["task_id"],
                bool(row["task_pass"]),
                str(row.get("reason", "")),
            )
        receipts = verify_receipt_chain(Path(receipts_path))

    return {
        "verdict": run_verdict,
        "rule_version": RULE_VERSION,
        "target": target,
        "arms": per_arm,
        "comparison": comparison,
        "discrepancies": discrepancies,
        "receipts": receipts,
    }


def render(result: Dict[str, Any]) -> str:
    lines = ["", "INDEPENDENT GRADE"]
    for summary in result["arms"]:
        split_parts: List[str] = []
        splits = summary.get("splits") or {}
        if "dev" in splits:
            d = splits["dev"]
            split_parts.append(
                f"dev {d['tasks_passed']}/{d['tasks_scored']} ({d['pass_at_1']:.0%})"
            )
        if "test" in splits:
            t = splits["test"]
            split_parts.append(
                f"test {t['tasks_passed']}/{t['tasks_scored']} ({t['pass_at_1']:.0%})"
            )
        split_note = f", {', '.join(split_parts)}" if split_parts else ""
        void_note = f", voided {summary['voided']}" if summary.get("voided") else ""
        lines.append(
            f"  {summary['arm']}: pass@1 {summary['pass_at_1']:.0%} "
            f"(lower bound {summary['pass_at_1_lower_95']:.0%}, "
            f"empty {summary['empty_answer_rate']:.0%}{void_note}{split_note}) "
            f"-> {summary['verdict']}"
        )
    comparison = result.get("comparison") or {}
    pairs = comparison.get("pairs") or []
    if len(pairs) > 1:
        for pair in pairs:
            lines.append(
                f"  comparison ({' vs '.join(pair['arms'])}): "
                f"{pair['discordant']} discordant of {pair['paired_tasks']} paired, "
                f"p = {pair['p_value']:.4f}"
            )
    elif comparison:
        lines.append(
            f"  comparison: {comparison['discordant']} discordant, "
            f"p = {comparison['p_value']:.4f}"
        )
    if result["discrepancies"]:
        lines.append("  RECONCILIATION FAILURES (fatal):")
        lines.extend(f"    - {d}" for d in result["discrepancies"])
    if result["receipts"].get("ok") is not None:
        lines.append(f"  receipts: {result['receipts']}")
    lines.append(f"  VERDICT: {result['verdict']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="the sealed grader - the only verdict")
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--target", type=float, default=0.70)
    parser.add_argument("--split", type=Path, default=None)
    parser.add_argument("--receipts", type=Path, default=None)
    args = parser.parse_args(argv)

    result = grade_run(
        args.rows,
        args.gold,
        target=args.target,
        split_path=args.split,
        receipts_path=args.receipts,
    )
    print(render(result))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
