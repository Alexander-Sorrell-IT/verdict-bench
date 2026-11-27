#!/usr/bin/env python3
# verdict-bench runner/report.py
#
# The report is GENERATED, not written. Every number comes from committed
# artifacts - the rows, the gold, the split, the receipts - and no figure is
# ever typed by hand. Regenerating from the same artifacts produces a
# byte-identical report (no timestamps anywhere in it).
#
#   python -m runner.report --rows results/run/results.jsonl \
#       --gold world/private/gold_world2.json --split spec/split.json \
#       --receipts results/run/receipts.json --target 0.70
#
# The verdict line comes from score/independent_grader.py and nothing else.

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR / "runner"))

from score.independent_grader import RULE_VERSION, grade_run  # noqa: E402
from stats import (  # noqa: E402
    discordant_tasks,
    index_by_task,
    load_attempts,
    load_voids,
    mcnemar,
    summarise_arm,
)
from receipts import verify_hashed_file, verify_receipt_chain  # noqa: E402

# What the world gates cannot check - carried verbatim so the report never
# claims more than the instrument proves.
GATE_BLIND_SPOTS = (
    "that the tasks are actually hard (a trivially guessable world passes every gate)",
    "that the answer formulas are absent from the MODEL's context window at runtime",
    "that an arm cannot reach gold through the model, a tool, or the OS",
    "that the gold is CORRECT - a verifiable wrong answer is still wrong",
    "that the run was fair (budget, load, retries) - that is bench.py's job",
)


def _sha(path: Optional[Path]) -> str:
    if path is None or not Path(path).is_file():
        return ""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_report(
    rows_path: Path,
    gold_path: Path,
    target: float = 0.70,
    split_path: Optional[Path] = None,
    receipts_path: Optional[Path] = None,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compose the report from the artifacts. Returns the data and the text."""
    rows: List[Dict[str, Any]] = []
    for line in Path(rows_path).read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))

    attempts = load_attempts(Path(rows_path))
    voids = load_voids(Path(rows_path))

    by_arm: Dict[str, List[Any]] = {}
    for attempt in attempts:
        by_arm.setdefault(attempt.arm, []).append(attempt)

    summaries = [summarise_arm(arm, by_arm[arm]) for arm in sorted(by_arm)]

    comparison: Dict[str, Any] = {}
    if len(by_arm) >= 2:
        # Pair on the tasks BOTH arms completed live: voiding removes different
        # tasks per arm (A: 22 live, B: 13 on the World-1 evidence), and an
        # unpaired McNemar is not a comparison. The pairing is disclosed.
        names = sorted(by_arm)
        pairs = []
        for i, first in enumerate(names):
            for second in names[i + 1 :]:
                a_map = index_by_task(by_arm[first])
                b_map = index_by_task(by_arm[second])
                shared = sorted(set(a_map) & set(b_map))
                a_live = [a_map[task_id] for task_id in shared]
                b_live = [b_map[task_id] for task_id in shared]
                result = mcnemar(a_live, b_live)
                pairs.append(
                    {
                        "arms": [first, second],
                        "paired_tasks": len(shared),
                        "discordant": result.discordant,
                        "p_value": result.p_value,
                        "tasks": [
                            {
                                "task_id": d.task_id,
                                "family": d.family,
                                "winner": d.winner,
                                "loser_scored_zero": d.loser_scored_zero,
                            }
                            for d in discordant_tasks(a_live, b_live)
                        ],
                    }
                )
        comparison = {"arms": names, "pairs": pairs}

    injected = [r for r in rows if r.get("adversary")]
    recovery = {
        "injected_tasks": len(injected),
        "recovered": sum(
            1 for r in injected if (r.get("recovery") or {}).get("recovered")
        ),
        "by_outcome": _tally(
            (r.get("recovery") or {}).get("answer_outcome") for r in injected
        ),
    }

    routes = {(r.get("model"), r.get("base_url")) for r in rows if r.get("model")}
    verdict = grade_run(
        Path(rows_path),
        Path(gold_path),
        target=target,
        split_path=Path(split_path) if split_path else None,
    )

    receipts: Dict[str, Any] = {"chain": None, "log": None}
    if receipts_path is not None and Path(receipts_path).is_file():
        receipts["chain"] = verify_receipt_chain(Path(receipts_path))
    if log_path is not None and receipts_path is not None:
        receipts["log"] = verify_hashed_file(Path(receipts_path), Path(log_path))

    data = {
        "rows_sha256": _sha(rows_path),
        "gold_sha256": _sha(gold_path),
        "split_sha256": _sha(split_path),
        "rows_total": len(rows),
        "voids": len(voids),
        "void_detail": voids,
        "summaries": summaries,
        "comparison": comparison,
        "recovery": recovery,
        "model_routes": sorted(r for r in routes if r[0]),
        "verdict": verdict,
        "receipts": receipts,
    }
    data["text"] = render(data)
    return data


def _tally(values: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def render(data: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# verdict-bench run report")
    lines.append("")
    lines.append("Generated by runner/report.py from the committed artifacts:")
    lines.append(f"  rows sha256: {data['rows_sha256'][:16] or '(no rows file)'}")
    lines.append(f"  gold sha256: {data['gold_sha256'][:16]}")
    lines.append(f"  split sha256: {data['split_sha256'][:16] or '(none)'}")
    lines.append("")

    lines.append("## Model route (from the rows, not from prose)")
    if data["model_routes"]:
        for model, base_url in data["model_routes"]:
            lines.append(f"  {model} via {base_url}")
    else:
        lines.append("  NOT RECORDED IN THE ROWS - the run predates the row field")
    lines.append("")

    lines.append("## The void-and-rerun policy, applied")
    lines.append(
        f"  {data['voids']} of {data['rows_total']} recorded attempts were voided "
        "(rc=124: killed by the wall clock, never graded on their merits)."
    )
    for void in data["void_detail"]:
        lines.append(
            f"  - {void['arm']}/{void['task_id']}: recorded task_pass="
            f"{void['task_pass']}, wall {void['wall_s']}s - {void['reason']}"
        )
    lines.append("")

    lines.append("## Arms")
    lines.append("| Arm | Tasks | Passed | Pass@1 | Wilson 95% lower | Criteria |")
    lines.append("|-----|-------|--------|--------|------------------|----------|")
    for summary in data["summaries"]:
        lines.append(
            f"| {summary['arm']} | {summary['tasks_scored']} | "
            f"{summary['tasks_passed']} | {summary['pass_at_1']:.0%} | "
            f"{summary['pass_at_1_lower_95']:.0%} | "
            f"{summary['criteria_passed']}/{summary['criteria_total']} |"
        )
    lines.append("")

    if data["comparison"]:
        comparison = data["comparison"]
        lines.append(
            f"## Comparison ({' vs '.join(comparison['arms'])}) - exact McNemar, "
            "paired on the tasks both arms completed live"
        )
        for pair in comparison["pairs"]:
            lines.append(
                f"  {' vs '.join(pair['arms'])}: {pair['discordant']} discordant "
                f"of {pair['paired_tasks']} paired, p = {pair['p_value']:.4f}"
            )
            for task in pair["tasks"]:
                lines.append(
                    f"    - {task['task_id']} ({task['family']}): "
                    f"{task['winner']} won, loser scored zero: "
                    f"{task['loser_scored_zero']}"
                )
        lines.append("")

    recovery = data["recovery"]
    lines.append("## Recovery, measured")
    if recovery["injected_tasks"]:
        lines.append(
            f"  {recovery['recovered']}/{recovery['injected_tasks']} recovered "
            "after the injected contradiction"
        )
        for outcome, count in recovery["by_outcome"].items():
            lines.append(f"  - {outcome}: {count}")
    else:
        lines.append("  no contradiction was injected in this run")
    lines.append("")

    lines.append("## Receipts")
    if data["receipts"]["chain"] is not None:
        chain = data["receipts"]["chain"]
        lines.append(f"  chain: {'OK' if chain['ok'] else 'BROKEN'} ({chain})")
    else:
        lines.append("  chain: not supplied")
    if data["receipts"]["log"] is not None:
        audit = data["receipts"]["log"]
        lines.append(f"  run-log audit: {'OK' if audit['ok'] else 'EDITED AFTER RUN'}")
    lines.append("")

    verdict = data["verdict"]
    lines.append(f"## Verdict (rule version {RULE_VERSION})")
    lines.append(f"  {verdict['verdict']}")
    if verdict["discrepancies"]:
        lines.append("  reconciliation failures (fatal):")
        lines.extend(f"    - {d}" for d in verdict["discrepancies"])
    lines.append("")

    lines.append("## Disclosures")
    lines.append(
        "  - Voided attempts are excluded from every number above; the recorded"
    )
    lines.append("    (pre-void) numbers are what the run log says, and are published")
    lines.append("    beside the void list above.")
    lines.append(
        "  - World 1 cannot be re-run: no task prompts, no world files, no gold"
    )
    lines.append(
        "    hashes, and the launcher that produced its results is gone. What the"
    )
    lines.append(
        "    recorded attempts support is this offline re-verification, nothing more."
    )
    lines.append("  - The gates cannot check:")
    for blind_spot in GATE_BLIND_SPOTS:
        lines.append(f"      - {blind_spot}")
    lines.append("")
    lines.append("  Every figure above regenerates from the committed artifacts by")
    lines.append("  `python -m runner.report`; no number was typed by hand.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="generate the run report")
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--target", type=float, default=0.70)
    parser.add_argument("--split", type=Path, default=None)
    parser.add_argument("--receipts", type=Path, default=None)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    data = build_report(
        args.rows,
        args.gold,
        target=args.target,
        split_path=args.split,
        receipts_path=args.receipts,
        log_path=args.log,
    )
    if args.out:
        args.out.write_text(data["text"])
        print(f"report: {args.out}")
    else:
        print(data["text"])
    return 0 if data["verdict"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
