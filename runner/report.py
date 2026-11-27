#!/usr/bin/env python3
# verdict-bench runner/report.py
#
# The report is GENERATED, not written. Every number comes from committed
# artifacts - the rows, the gold, the split, the receipts - and no figure is
# ever typed by hand. Regenerating from the same artifacts produces a
# byte-identical Markdown report AND a byte-identical standalone HTML scorecard
# (no timestamps anywhere in either output).
#
#   python -m runner.report --rows results/run/results.jsonl \
#       --gold world/private/gold_world2.json --split spec/split.json \
#       --receipts results/run/receipts.json --target 0.70 \
#       --out results/run/REPORT.md --html results/run/REPORT.html
#
# The verdict line comes from score/independent_grader.py and nothing else.

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR / "runner"))

from receipts import verify_hashed_file, verify_receipt_chain  # noqa: E402
from score.independent_grader import RULE_VERSION, grade_run  # noqa: E402
from stats import (  # noqa: E402
    discordant_tasks,
    index_by_task,
    is_void,
    load_attempts,
    load_voids,
    mcnemar,
    summarise_arm,
)

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


def _load_split_meta(split_path: Optional[Path]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Return (task_id -> split, task_id -> domain) from spec/split.json."""
    if split_path is None or not Path(split_path).is_file():
        return {}, {}
    doc = json.loads(Path(split_path).read_text())
    task_split: Dict[str, str] = {}
    task_domain: Dict[str, str] = {}
    for split_name in ("dev", "test"):
        for entry in doc.get(split_name, []):
            tid = str(entry.get("task_id") or "").strip()
            if not tid:
                continue
            task_split[tid] = split_name
            dom = str(entry.get("domain") or "").strip()
            if dom:
                task_domain[tid] = dom
    return task_split, task_domain


def _tally(values: Any) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _row_valid_json(row: Dict[str, Any]) -> bool:
    if "valid_json" in row and row["valid_json"] is not None:
        return bool(row["valid_json"])
    answer = row.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        return False
    try:
        json.loads(answer)
        return True
    except json.JSONDecodeError:
        return False


def _gate_name_from_block(entry: Any) -> str:
    text = str(entry)
    head = text.split(":", 1)[0].strip()
    if "/" in head:
        return head.split("/", 1)[1].strip() or head
    return head or "unknown"


def _gate_name_from_repair(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("gate") or "unknown")
    return _gate_name_from_block(entry)


def _resolve_domain(row: Dict[str, Any], task_domain: Dict[str, str]) -> str:
    raw = str(row.get("family") or "").strip()
    if raw and raw != "unknown":
        return raw
    tid = str(row.get("task_id") or "")
    return task_domain.get(tid, "unknown")


def _resolve_split(row: Dict[str, Any], task_split: Dict[str, str]) -> str:
    tid = str(row.get("task_id") or "")
    if tid in task_split:
        return task_split[tid]
    raw = str(row.get("split") or "").strip().lower()
    if raw and raw != "unknown":
        return raw
    return "unknown"


def _avg_optional(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _sum_optional(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return round(sum(values), 2)


def _fmt_opt(value: Optional[float], decimals: int = 1, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}{suffix}"


def _fmt_split(splits: Dict[str, Dict[str, Any]], key: str) -> str:
    bucket = splits.get(key)
    if not bucket or not bucket.get("tasks_scored"):
        return "—"
    return f"{bucket['tasks_passed']}/{bucket['tasks_scored']} ({bucket['pass_at_1']:.0%})"


def _split_tag(split_name: str) -> str:
    if split_name == "dev":
        return "[DEV]"
    if split_name == "test":
        return "[TEST]"
    return "[—]"


def build_report(
    rows_path: Path,
    gold_path: Path,
    target: float = 0.70,
    split_path: Optional[Path] = None,
    receipts_path: Optional[Path] = None,
    log_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Compose the APEX-style report from committed artifacts. Returns data + Markdown + HTML."""
    rows: List[Dict[str, Any]] = []
    for line in Path(rows_path).read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))

    attempts = load_attempts(Path(rows_path))
    voids = load_voids(Path(rows_path))
    task_split, task_domain = _load_split_meta(split_path)

    by_arm: Dict[str, List[Any]] = {}
    for attempt in attempts:
        by_arm.setdefault(attempt.arm, []).append(attempt)

    verdict = grade_run(
        Path(rows_path),
        Path(gold_path),
        target=target,
        split_path=Path(split_path) if split_path else None,
    )
    verdict_by_arm = {entry["arm"]: entry for entry in verdict["arms"]}

    summaries: List[Dict[str, Any]] = []
    failure_modes: Dict[str, Dict[str, Any]] = {}
    budget_arms: List[Dict[str, Any]] = []

    for arm in sorted(by_arm):
        arm_rows = [r for r in rows if r.get("arm") == arm]
        live_rows = [r for r in arm_rows if not is_void(r)]
        summary = summarise_arm(arm, by_arm[arm])
        v_entry = verdict_by_arm.get(arm, {})

        valid_json_count = sum(1 for r in live_rows if _row_valid_json(r))
        valid_json_rate = valid_json_count / len(live_rows) if live_rows else 0.0

        steps_vals = [
            float(r["steps"])
            for r in live_rows
            if isinstance(r.get("steps"), (int, float))
        ]
        calls_vals = [
            float(r["model_calls"])
            for r in live_rows
            if isinstance(r.get("model_calls"), (int, float))
        ]
        wall_vals = [
            float(r["wall_s"])
            for r in live_rows
            if isinstance(r.get("wall_s"), (int, float))
        ]
        gate_block_list = [
            b for r in live_rows for b in (r.get("gate_blocks") or [])
        ]
        repair_list = [
            rep for r in live_rows for rep in (r.get("repairs") or [])
        ]
        overflowed_count = sum(1 for r in live_rows if r.get("overflowed"))
        empty_count = sum(
            1 for r in live_rows if r.get("answer_empty", not r.get("answer"))
        )

        summary.update(
            {
                "voided": len(arm_rows) - len(live_rows),
                "empty_answer_rate": v_entry.get("empty_answer_rate", 0.0),
                "verdict": v_entry.get("verdict", "REJECT"),
                "splits": v_entry.get("splits", {}),
                "valid_json_count": valid_json_count,
                "valid_json_rate": round(valid_json_rate, 4),
                "avg_steps": _avg_optional(steps_vals),
                "total_steps": _sum_optional(steps_vals),
                "avg_calls": _avg_optional(calls_vals),
                "total_calls": _sum_optional(calls_vals),
                "avg_wall_s": _avg_optional(wall_vals),
                "total_wall_s": _sum_optional(wall_vals),
                "gate_blocks": len(gate_block_list),
                "repairs": len(repair_list),
                "overflowed": overflowed_count,
            }
        )
        summaries.append(summary)

        failed_live = [r for r in live_rows if not r.get("task_pass")]
        failure_modes[arm] = {
            "live_tasks": len(live_rows),
            "failed_tasks": len(failed_live),
            "empty_answers": empty_count,
            "invalid_json": len(live_rows) - valid_json_count,
            "overflowed": overflowed_count,
            "stopped_reasons": _tally(
                (r.get("stopped_reason") or ("answered" if r.get("task_pass") else "unrecorded"))
                for r in live_rows
            ),
            "failed_stopped_reasons": _tally(
                (r.get("stopped_reason") or "unrecorded") for r in failed_live
            ),
            "gate_blocks_by_gate": _tally(
                _gate_name_from_block(b) for b in gate_block_list
            ),
            "repairs_by_gate": _tally(
                _gate_name_from_repair(rep) for rep in repair_list
            ),
        }

        arm_models = sorted({str(r["model"]) for r in arm_rows if r.get("model")})
        arm_urls = sorted({str(r["base_url"]) for r in arm_rows if r.get("base_url")})
        budget_arms.append(
            {
                "arm": arm,
                "models": arm_models,
                "base_urls": arm_urls,
                "total_steps": summary["total_steps"],
                "total_calls": summary["total_calls"],
                "total_wall_s": summary["total_wall_s"],
                "overflowed": overflowed_count,
                "voided": summary["voided"],
            }
        )

    # Ranked Executive Leaderboard (highest pass@1, then held-out test pass@1,
    # then Wilson lower bound, then criteria rate, then arm name).
    ranked = sorted(
        summaries,
        key=lambda s: (
            -float(s["pass_at_1"]),
            -float((s.get("splits") or {}).get("test", {}).get("pass_at_1", -1.0)),
            -float(s["pass_at_1_lower_95"]),
            -float(s["criteria_rate"]),
            str(s["arm"]),
        ),
    )
    leaderboard: List[Dict[str, Any]] = []
    for idx, item in enumerate(ranked, start=1):
        entry = dict(item)
        entry["rank"] = idx
        leaderboard.append(entry)

    # Per-domain capability matrix (enriched from split.json when rows omit family).
    domain_tasks: Dict[str, Dict[str, str]] = {}
    domain_arm_rows: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        if is_void(row):
            continue
        dom = _resolve_domain(row, task_domain)
        tid = str(row.get("task_id") or "")
        spl = _resolve_split(row, task_split)
        domain_tasks.setdefault(dom, {})[tid] = spl
        arm = str(row.get("arm") or "")
        domain_arm_rows.setdefault((dom, arm), []).append(row)

    arm_order = [entry["arm"] for entry in leaderboard]
    domains: List[Dict[str, Any]] = []
    for dom in sorted(domain_tasks):
        tmap = domain_tasks[dom]
        arm_stats: Dict[str, Dict[str, Any]] = {}
        for arm in arm_order:
            drows = domain_arm_rows.get((dom, arm), [])
            scored = len(drows)
            passed = sum(1 for r in drows if r.get("task_pass"))
            c_pass = sum(int(r.get("criteria_passed", 0)) for r in drows)
            c_tot = sum(int(r.get("criteria_total", 0)) for r in drows)
            arm_stats[arm] = {
                "passed": passed,
                "scored": scored,
                "rate": (passed / scored) if scored else 0.0,
                "criteria_passed": c_pass,
                "criteria_total": c_tot,
            }
        domains.append(
            {
                "domain": dom,
                "total_tasks": len(tmap),
                "dev_tasks": sum(1 for v in tmap.values() if v == "dev"),
                "test_tasks": sum(1 for v in tmap.values() if v == "test"),
                "arms": arm_stats,
            }
        )

    # Pairwise exact McNemar & discordant task attribution with REAL arm names.
    comparison: Dict[str, Any] = {}
    if len(by_arm) >= 2:
        names = sorted(by_arm)
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
                    disc = discordant_tasks(
                        a_live, b_live, a_name=first, b_name=second
                    )
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
                            "significant": result.p_value < 0.05,
                            "tasks": [
                                {
                                    "task_id": d.task_id,
                                    "family": (
                                        d.family
                                        if d.family and d.family != "unknown"
                                        else task_domain.get(d.task_id, "unknown")
                                    ),
                                    "split": task_split.get(d.task_id, "unknown"),
                                    "winner": d.winner,
                                    "loser": d.loser,
                                    "winner_criteria": d.winner_criteria,
                                    "loser_criteria": d.loser_criteria,
                                    "loser_scored_zero": d.loser_scored_zero,
                                }
                                for d in disc
                            ],
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
                            "significant": False,
                            "tasks": [],
                        }
                    )
        comparison = {"arms": names, "pairs": pairs}

    # Full task outcome matrix ([DEV] / [TEST]).
    rows_by_task_arm: Dict[Tuple[str, str], Dict[str, Any]] = {}
    task_ids_set: set[str] = set()
    task_row_domain: Dict[str, str] = {}
    task_row_split: Dict[str, str] = {}
    for row in rows:
        tid = str(row.get("task_id") or "")
        arm = str(row.get("arm") or "")
        if not tid:
            continue
        task_ids_set.add(tid)
        dom = _resolve_domain(row, task_domain)
        if tid not in task_row_domain or task_row_domain[tid] == "unknown":
            task_row_domain[tid] = dom
        spl = _resolve_split(row, task_split)
        if tid not in task_row_split or task_row_split[tid] == "unknown":
            task_row_split[tid] = spl
        # Prefer a live row over a voided row if both exist for the same (task, arm).
        existing = rows_by_task_arm.get((tid, arm))
        if existing is None or (is_void(existing) and not is_void(row)):
            rows_by_task_arm[(tid, arm)] = row

    task_matrix: List[Dict[str, Any]] = []
    for tid in sorted(task_ids_set):
        outcomes: Dict[str, Dict[str, Any]] = {}
        for arm in arm_order:
            row_opt = rows_by_task_arm.get((tid, arm))
            if row_opt is None:
                outcomes[arm] = {
                    "status": "N/A",
                    "task_pass": None,
                    "voided": False,
                    "stopped_reason": "",
                    "steps": None,
                    "model_calls": None,
                    "wall_s": None,
                    "gate_blocks": 0,
                    "repairs": 0,
                    "reason": "",
                }
            elif is_void(row_opt):
                outcomes[arm] = {
                    "status": "VOID",
                    "task_pass": bool(row_opt.get("task_pass")),
                    "voided": True,
                    "stopped_reason": str(row_opt.get("stopped_reason") or f"rc={row_opt.get('rc')}"),
                    "steps": row_opt.get("steps"),
                    "model_calls": row_opt.get("model_calls"),
                    "wall_s": row_opt.get("wall_s"),
                    "gate_blocks": len(row_opt.get("gate_blocks") or []),
                    "repairs": len(row_opt.get("repairs") or []),
                    "reason": str(row_opt.get("reason") or ""),
                }
            else:
                passed_flag = bool(row_opt.get("task_pass"))
                outcomes[arm] = {
                    "status": "PASS" if passed_flag else "FAIL",
                    "task_pass": passed_flag,
                    "voided": False,
                    "stopped_reason": str(row_opt.get("stopped_reason") or ""),
                    "steps": row_opt.get("steps"),
                    "model_calls": row_opt.get("model_calls"),
                    "wall_s": row_opt.get("wall_s"),
                    "gate_blocks": len(row_opt.get("gate_blocks") or []),
                    "repairs": len(row_opt.get("repairs") or []),
                    "reason": str(row_opt.get("reason") or ""),
                }
        task_matrix.append(
            {
                "task_id": tid,
                "split": task_row_split.get(tid, "unknown"),
                "domain": task_row_domain.get(tid, "unknown"),
                "outcomes": outcomes,
            }
        )

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
    distinct_models = sorted({str(r["model"]) for r in rows if r.get("model")})
    budget_parity = {
        "arms": budget_arms,
        "distinct_models": distinct_models,
        "same_model": (len(distinct_models) == 1) if distinct_models else None,
        "total_overflowed": sum(a["overflowed"] for a in budget_arms),
    }

    receipts: Dict[str, Any] = {"chain": None, "log": None}
    if receipts_path is not None and Path(receipts_path).is_file():
        receipts["chain"] = verify_receipt_chain(Path(receipts_path))
    if log_path is not None and receipts_path is not None:
        receipts["log"] = verify_hashed_file(Path(receipts_path), Path(log_path))

    data: Dict[str, Any] = {
        "rule_version": RULE_VERSION,
        "target": target,
        "rows_sha256": _sha(rows_path),
        "gold_sha256": _sha(gold_path),
        "split_sha256": _sha(split_path),
        "rows_total": len(rows),
        "voids": len(voids),
        "void_detail": voids,
        "summaries": summaries,
        "leaderboard": leaderboard,
        "domains": domains,
        "comparison": comparison,
        "task_matrix": task_matrix,
        "failure_modes": failure_modes,
        "budget_parity": budget_parity,
        "recovery": recovery,
        "model_routes": sorted(r for r in routes if r[0]),
        "verdict": verdict,
        "receipts": receipts,
    }
    data["text"] = render(data)
    data["html"] = render_html(data)
    return data


def render(data: Dict[str, Any]) -> str:
    """Render deterministic Mercor APEX-Agents style Markdown report."""
    lines: List[str] = []
    verdict = data["verdict"]
    leaderboard = data["leaderboard"]
    arm_order = [entry["arm"] for entry in leaderboard]

    lines.append("# verdict-bench — APEX Evaluation Report")
    lines.append("")
    lines.append(
        f"> **Verdict:** `{verdict['verdict']}` | "
        f"**Rule Version:** `{RULE_VERSION}` | "
        f"**Target Pass@1 (95% Wilson LB):** `{data['target']:.0%}` | "
        f"**Attempts:** `{data['rows_total']}` (`{data['voids']}` voided)"
    )
    lines.append("")
    lines.append("Generated deterministically by `runner/report.py` from committed artifacts:")
    lines.append(f"- **rows sha256:** `{data['rows_sha256'][:16] or '(no rows file)'}`")
    lines.append(f"- **gold sha256:** `{data['gold_sha256'][:16]}`")
    lines.append(f"- **split sha256:** `{data['split_sha256'][:16] or '(none)'}`")
    if data["model_routes"]:
        routes_str = ", ".join(f"`{m}` via `{u}`" for m, u in data["model_routes"])
        lines.append(f"- **model route(s):** {routes_str}")
    else:
        lines.append(
            "- **model route(s):** NOT RECORDED IN THE ROWS - the run predates the row field"
        )
    lines.append("")

    # 1. Executive Leaderboard
    lines.append("## 1. Executive Leaderboard")
    lines.append("")
    lines.append(
        "| Rank | Arm | Overall Pass@1 | Wilson 95% LB | Dev Pass@1 | Held-Out Test Pass@1 "
        "| Criteria | Valid JSON | Avg Steps | Avg Calls | Avg Wall | Gate Blocks / Repairs | Verdict |"
    )
    lines.append(
        "|------|-----|----------------|---------------|------------|----------------------"
        "|----------|------------|-----------|-----------|----------|-----------------------|---------|"
    )
    for row in leaderboard:
        splits = row.get("splits") or {}
        lines.append(
            f"| #{row['rank']} | **{row['arm']}** "
            f"| {row['tasks_passed']}/{row['tasks_scored']} ({row['pass_at_1']:.0%}) "
            f"| {row['pass_at_1_lower_95']:.0%} "
            f"| {_fmt_split(splits, 'dev')} "
            f"| {_fmt_split(splits, 'test')} "
            f"| {row['criteria_passed']}/{row['criteria_total']} "
            f"| {row['valid_json_rate']:.0%} "
            f"| {_fmt_opt(row['avg_steps'], 1)} "
            f"| {_fmt_opt(row['avg_calls'], 1)} "
            f"| {_fmt_opt(row['avg_wall_s'], 1, 's')} "
            f"| {row['gate_blocks']} / {row['repairs']} "
            f"| **{row['verdict']}** |"
        )
    lines.append("")

    # 2. Per-Domain Capability Matrix
    lines.append("## 2. Per-Domain Capability Matrix")
    lines.append("")
    if data["domains"]:
        header = "| Domain | Tasks (Dev/Test) | " + " | ".join(arm_order) + " |"
        sep = "|--------|------------------|" + "|".join(["------"] * len(arm_order)) + "|"
        lines.append(header)
        lines.append(sep)
        for dom in data["domains"]:
            mix = (
                f"{dom['total_tasks']} ({dom['dev_tasks']}/{dom['test_tasks']})"
                if (dom["dev_tasks"] or dom["test_tasks"])
                else str(dom["total_tasks"])
            )
            cells = []
            for arm in arm_order:
                st = dom["arms"][arm]
                if st["scored"]:
                    cells.append(f"{st['passed']}/{st['scored']} ({st['rate']:.0%})")
                else:
                    cells.append("—")
            lines.append(f"| {dom['domain']} | {mix} | " + " | ".join(cells) + " |")
    else:
        lines.append("No live domain rows available.")
    lines.append("")

    # 3. Pairwise Exact McNemar & Discordant Task Attribution
    if data["comparison"]:
        comparison = data["comparison"]
        lines.append(
            f"## 3. Pairwise Exact McNemar ({' vs '.join(comparison['arms'])})"
        )
        lines.append("")
        lines.append(
            "Paired strictly on tasks both arms completed live (excluding voids)."
        )
        lines.append("")
        lines.append(
            "| Pair | Paired | First Only | Second Only | Both Pass | Both Fail "
            "| Discordant | Exact p-value | Sig (α=0.05) |"
        )
        lines.append(
            "|------|--------|------------|-------------|-----------|-----------"
            "|------------|---------------|--------------|"
        )
        for pair in comparison["pairs"]:
            pair_label = f"{pair['arms'][0]} vs {pair['arms'][1]}"
            sig = "YES" if pair["significant"] else "no"
            lines.append(
                f"| {pair_label} | {pair['paired_tasks']} | {pair['a_only']} "
                f"| {pair['b_only']} | {pair['both_pass']} | {pair['both_fail']} "
                f"| {pair['discordant']} | {pair['p_value']:.4f} | {sig} |"
            )
        lines.append("")
        for pair in comparison["pairs"]:
            pair_label = f"{pair['arms'][0]} vs {pair['arms'][1]}"
            lines.append(
                f"### Discordant Tasks: {pair_label} "
                f"({pair['discordant']} of {pair['paired_tasks']} paired, "
                f"p = {pair['p_value']:.4f})"
            )
            if not pair["tasks"]:
                lines.append("- Perfect agreement on all paired tasks.")
            else:
                for task in pair["tasks"]:
                    tag = _split_tag(task["split"])
                    lines.append(
                        f"- {task['task_id']} {tag} ({task['family']}): "
                        f"**{task['winner']}** won over {task['loser']} "
                        f"({task['winner_criteria']} vs {task['loser_criteria']}, "
                        f"loser scored zero: {task['loser_scored_zero']})"
                    )
            lines.append("")

    # 4. Full Task Outcome Matrix
    lines.append("## 4. Full Task Outcome Matrix")
    lines.append("")
    if data["task_matrix"]:
        header = "| Task | Split | Domain | " + " | ".join(arm_order) + " |"
        sep = "|------|-------|--------|" + "|".join(["------"] * len(arm_order)) + "|"
        lines.append(header)
        lines.append(sep)
        for row in data["task_matrix"]:
            cells = []
            for arm in arm_order:
                out = row["outcomes"][arm]
                st = out["status"]
                if st == "PASS":
                    extra = (
                        f" ({out['steps']}s/{out['model_calls']}c)"
                        if out["steps"] is not None and out["model_calls"] is not None
                        else ""
                    )
                    cells.append(f"PASS{extra}")
                elif st == "FAIL":
                    why = out["stopped_reason"] or "wrong"
                    cells.append(f"FAIL ({why})")
                elif st == "VOID":
                    why = out["stopped_reason"] or "void"
                    cells.append(f"VOID ({why})")
                else:
                    cells.append("—")
            lines.append(
                f"| {row['task_id']} | {_split_tag(row['split'])} | {row['domain']} | "
                + " | ".join(cells)
                + " |"
            )
    lines.append("")

    # 5. Failure-Mode Telemetry & Gate Activity
    lines.append("## 5. Failure-Mode Telemetry & Gate Activity")
    lines.append("")
    lines.append(
        "| Arm | Live | Failed | Empty Answers | Invalid JSON | Overflows "
        "| Stopped Reasons (Failures) | Gate Blocks | Gate Repairs |"
    )
    lines.append(
        "|-----|------|--------|---------------|--------------|-----------"
        "|----------------------------|-------------|--------------|"
    )
    for arm in arm_order:
        fm = data["failure_modes"][arm]
        fail_reasons = (
            ", ".join(f"{k}: {v}" for k, v in fm["failed_stopped_reasons"].items())
            if fm["failed_stopped_reasons"]
            else "none"
        )
        blocks_str = (
            ", ".join(f"{k}: {v}" for k, v in fm["gate_blocks_by_gate"].items())
            if fm["gate_blocks_by_gate"]
            else "none"
        )
        repairs_str = (
            ", ".join(f"{k}: {v}" for k, v in fm["repairs_by_gate"].items())
            if fm["repairs_by_gate"]
            else "none"
        )
        lines.append(
            f"| {arm} | {fm['live_tasks']} | {fm['failed_tasks']} "
            f"| {fm['empty_answers']} | {fm['invalid_json']} | {fm['overflowed']} "
            f"| {fail_reasons} | {blocks_str} | {repairs_str} |"
        )
    lines.append("")

    # 6. Void-and-Rerun Policy Audit
    lines.append("## 6. Void-and-Rerun Policy Audit")
    lines.append("")
    lines.append(
        f"{data['voids']} of {data['rows_total']} recorded attempts were voided "
        "(wall-clock timeout rc=124 or transport-dead empty attempt, never graded on their merits)."
    )
    if data["void_detail"]:
        lines.append("")
        for void in data["void_detail"]:
            lines.append(
                f"- {void['arm']}/{void['task_id']}: recorded task_pass="
                f"{void['task_pass']}, wall {void['wall_s']}s - {void['reason']}"
            )
    lines.append("")

    # 7. Recovery, Measured
    recovery = data["recovery"]
    lines.append("## 7. Recovery, Measured")
    lines.append("")
    if recovery["injected_tasks"]:
        lines.append(
            f"{recovery['recovered']}/{recovery['injected_tasks']} recovered "
            "after the injected contradiction."
        )
        for outcome, count in recovery["by_outcome"].items():
            lines.append(f"- {outcome}: {count}")
    else:
        lines.append("No contradiction was injected in this run.")
    lines.append("")

    # 8. Budget Parity & Cryptographic Custody
    lines.append("## 8. Budget Parity & Cryptographic Custody")
    lines.append("")
    bp = data["budget_parity"]
    model_parity_label = (
        f"OK (matched: `{bp['distinct_models'][0]}`)"
        if bp["same_model"] is True
        else (
            f"DIVERGED ({', '.join(bp['distinct_models'])})"
            if bp["same_model"] is False
            else "NOT RECORDED IN ROWS"
        )
    )
    lines.append(f"- **Model parity:** {model_parity_label}")
    lines.append(f"- **Budget overflows:** {bp['total_overflowed']}")
    if data["receipts"]["chain"] is not None:
        chain = data["receipts"]["chain"]
        lines.append(
            f"- **Receipt chain:** {'OK' if chain['ok'] else 'BROKEN'} (`{chain}`)"
        )
    else:
        lines.append("- **Receipt chain:** not supplied")
    if data["receipts"]["log"] is not None:
        audit = data["receipts"]["log"]
        lines.append(
            f"- **Run-log audit:** {'OK' if audit['ok'] else 'EDITED AFTER RUN'}"
        )
    lines.append("")
    lines.append(
        "| Arm | Models | Total Steps | Total Model Calls | Total Wall (s) | Overflows | Voided |"
    )
    lines.append(
        "|-----|--------|-------------|-------------------|----------------|-----------|--------|"
    )
    for arm_bp in bp["arms"]:
        models_str = ", ".join(arm_bp["models"]) if arm_bp["models"] else "—"
        lines.append(
            f"| {arm_bp['arm']} | {models_str} "
            f"| {_fmt_opt(arm_bp['total_steps'], 0)} "
            f"| {_fmt_opt(arm_bp['total_calls'], 0)} "
            f"| {_fmt_opt(arm_bp['total_wall_s'], 1, 's')} "
            f"| {arm_bp['overflowed']} | {arm_bp['voided']} |"
        )
    lines.append("")

    lines.append(f"### Independent Verdict (rule version {RULE_VERSION})")
    lines.append("")
    lines.append(f"- **VERDICT:** `{verdict['verdict']}`")
    if verdict["discrepancies"]:
        lines.append("- **Reconciliation failures (fatal):**")
        lines.extend(f"  - {d}" for d in verdict["discrepancies"])
    lines.append("")

    # 9. Disclosures & Gate Blind Spots
    lines.append("## 9. Disclosures & Gate Blind Spots")
    lines.append("")
    lines.append(
        "- Voided attempts are excluded from every live score above; the recorded "
        "(pre-void) outcomes are published in Section 6."
    )
    lines.append(
        "- World 1 cannot be re-run: no task prompts, no world files, no gold "
        "hashes, and the launcher that produced its results is gone. What the "
        "recorded attempts support is offline re-verification, nothing more."
    )
    lines.append("- The gates cannot check:")
    for blind_spot in GATE_BLIND_SPOTS:
        lines.append(f"  - {blind_spot}")
    lines.append("")
    lines.append(
        "Every figure above regenerates byte-identically from the committed artifacts via "
        "`python -m runner.report`; no number was typed by hand."
    )
    return "\n".join(lines)


def _esc(val: Any) -> str:
    return html.escape(str(val), quote=True)


def _verdict_class(verdict_str: str) -> str:
    v = verdict_str.upper()
    if v == "PASS":
        return "badge-pass"
    if v == "INCONCLUSIVE":
        return "badge-warn"
    return "badge-fail"


def _heat_class(rate: float, scored: int) -> str:
    if not scored:
        return "heat-na"
    if rate >= 0.80:
        return "heat-high"
    if rate >= 0.40:
        return "heat-mid"
    return "heat-low"


def render_html(data: Dict[str, Any]) -> str:
    """Render a standalone, zero-dependency, byte-deterministic APEX HTML scorecard."""
    verdict = data["verdict"]
    leaderboard = data["leaderboard"]
    arm_order = [entry["arm"] for entry in leaderboard]
    top = leaderboard[0] if leaderboard else None

    # Best held-out test arm (if test split exists)
    test_leader = None
    for entry in leaderboard:
        if "test" in (entry.get("splits") or {}):
            if test_leader is None or (
                entry["splits"]["test"]["pass_at_1"]
                > test_leader["splits"]["test"]["pass_at_1"]
            ):
                test_leader = entry

    bp = data["budget_parity"]
    chain = data["receipts"]["chain"]
    custody_label = (
        "VERIFIED"
        if (chain is not None and chain.get("ok"))
        else ("BROKEN" if chain is not None else "NOT SUPPLIED")
    )
    custody_cls = (
        "badge-pass"
        if custody_label == "VERIFIED"
        else ("badge-fail" if custody_label == "BROKEN" else "badge-muted")
    )

    routes_html = (
        " &middot; ".join(
            f"<code>{_esc(m)}</code> via <code>{_esc(u)}</code>"
            for m, u in data["model_routes"]
        )
        if data["model_routes"]
        else "<span class='muted'>Not recorded in rows</span>"
    )

    # Leaderboard rows
    lb_rows: List[str] = []
    for row in leaderboard:
        pct = round(row["pass_at_1"] * 100.0, 1)
        splits = row.get("splits") or {}
        lb_rows.append(
            "<tr>"
            f"<td class='mono rank'>#{row['rank']}</td>"
            f"<td class='arm-name'>{_esc(row['arm'])}</td>"
            "<td>"
            f"<div class='bar-wrap'><div class='bar-fill' style='width:{pct:.1f}%'></div></div>"
            f"<span class='mono'>{row['tasks_passed']}/{row['tasks_scored']} ({row['pass_at_1']:.0%})</span>"
            "</td>"
            f"<td class='mono'>{row['pass_at_1_lower_95']:.0%}</td>"
            f"<td class='mono'>{_esc(_fmt_split(splits, 'dev'))}</td>"
            f"<td class='mono'>{_esc(_fmt_split(splits, 'test'))}</td>"
            f"<td class='mono'>{row['criteria_passed']}/{row['criteria_total']}</td>"
            f"<td class='mono'>{row['valid_json_rate']:.0%}</td>"
            f"<td class='mono'>{_esc(_fmt_opt(row['avg_steps'], 1))}</td>"
            f"<td class='mono'>{_esc(_fmt_opt(row['avg_calls'], 1))}</td>"
            f"<td class='mono'>{_esc(_fmt_opt(row['avg_wall_s'], 1, 's'))}</td>"
            f"<td class='mono'>{row['gate_blocks']} / {row['repairs']}</td>"
            f"<td><span class='badge {_verdict_class(row['verdict'])}'>{_esc(row['verdict'])}</span></td>"
            "</tr>"
        )

    # Domain matrix rows
    dom_head = "".join(f"<th>{_esc(a)}</th>" for a in arm_order)
    dom_rows: List[str] = []
    for dom in data["domains"]:
        mix = (
            f"{dom['total_tasks']} ({dom['dev_tasks']}D/{dom['test_tasks']}T)"
            if (dom["dev_tasks"] or dom["test_tasks"])
            else str(dom["total_tasks"])
        )
        cells = []
        for arm in arm_order:
            st = dom["arms"][arm]
            cls = _heat_class(st["rate"], st["scored"])
            label = (
                f"{st['passed']}/{st['scored']} ({st['rate']:.0%})"
                if st["scored"]
                else "—"
            )
            cells.append(f"<td class='mono {cls}'>{_esc(label)}</td>")
        dom_rows.append(
            f"<tr><td>{_esc(dom['domain'])}</td><td class='mono muted'>{_esc(mix)}</td>"
            + "".join(cells)
            + "</tr>"
        )

    # Pairwise McNemar section
    mc_section = ""
    if data["comparison"]:
        mc_rows: List[str] = []
        disc_blocks: List[str] = []
        for pair in data["comparison"]["pairs"]:
            pair_name = f"{pair['arms'][0]} vs {pair['arms'][1]}"
            sig_badge = (
                "<span class='badge badge-pass'>p &lt; 0.05</span>"
                if pair["significant"]
                else "<span class='badge badge-muted'>n.s.</span>"
            )
            mc_rows.append(
                "<tr>"
                f"<td><strong>{_esc(pair_name)}</strong></td>"
                f"<td class='mono'>{pair['paired_tasks']}</td>"
                f"<td class='mono'>{pair['a_only']}</td>"
                f"<td class='mono'>{pair['b_only']}</td>"
                f"<td class='mono'>{pair['both_pass']}</td>"
                f"<td class='mono'>{pair['both_fail']}</td>"
                f"<td class='mono'>{pair['discordant']}</td>"
                f"<td class='mono'>{pair['p_value']:.4f}</td>"
                f"<td>{sig_badge}</td>"
                "</tr>"
            )
            if pair["tasks"]:
                items = "".join(
                    "<li>"
                    f"<span class='mono'>{_esc(t['task_id'])}</span> "
                    f"<span class='pill pill-{_esc(t['split'])}'>{_esc(_split_tag(t['split']))}</span> "
                    f"<span class='muted'>({_esc(t['family'])})</span> — "
                    f"<strong>{_esc(t['winner'])}</strong> beat {_esc(t['loser'])} "
                    f"<span class='mono muted'>({_esc(t['winner_criteria'])} vs {_esc(t['loser_criteria'])})</span>"
                    "</li>"
                    for t in pair["tasks"]
                )
                disc_blocks.append(
                    f"<div class='disc-card'><h4>{_esc(pair_name)} — {pair['discordant']} discordant</h4><ul>{items}</ul></div>"
                )
            else:
                disc_blocks.append(
                    f"<div class='disc-card'><h4>{_esc(pair_name)}</h4><p class='muted'>Perfect agreement on all {pair['paired_tasks']} paired tasks.</p></div>"
                )
        mc_section = (
            "<section class='card'>"
            "<h2>3. Pairwise Exact McNemar &amp; Discordant Attribution</h2>"
            "<div class='table-scroll'><table><thead><tr>"
            "<th>Pair</th><th>Paired</th><th>First Only</th><th>Second Only</th>"
            "<th>Both Pass</th><th>Both Fail</th><th>Discordant</th><th>Exact p</th><th>Sig</th>"
            "</tr></thead><tbody>"
            + "".join(mc_rows)
            + "</tbody></table></div>"
            "<div class='disc-grid'>"
            + "".join(disc_blocks)
            + "</div></section>"
        )

    # Task outcome matrix
    task_head = "".join(f"<th>{_esc(a)}</th>" for a in arm_order)
    task_rows: List[str] = []
    for row in data["task_matrix"]:
        spl = row["split"]
        cells = []
        for arm in arm_order:
            out = row["outcomes"][arm]
            st = out["status"]
            if st == "PASS":
                sub = (
                    f"<div class='sub mono'>{out['steps']}s / {out['model_calls']}c</div>"
                    if out["steps"] is not None and out["model_calls"] is not None
                    else ""
                )
                cells.append(f"<td><span class='badge badge-pass'>PASS</span>{sub}</td>")
            elif st == "FAIL":
                why = out["stopped_reason"] or "wrong"
                cells.append(
                    f"<td><span class='badge badge-fail'>FAIL</span>"
                    f"<div class='sub mono'>{_esc(why)}</div></td>"
                )
            elif st == "VOID":
                why = out["stopped_reason"] or "void"
                cells.append(
                    f"<td><span class='badge badge-warn'>VOID</span>"
                    f"<div class='sub mono'>{_esc(why)}</div></td>"
                )
            else:
                cells.append("<td class='muted'>—</td>")
        task_rows.append(
            "<tr>"
            f"<td class='mono'><strong>{_esc(row['task_id'])}</strong></td>"
            f"<td><span class='pill pill-{_esc(spl)}'>{_esc(_split_tag(spl))}</span></td>"
            f"<td>{_esc(row['domain'])}</td>"
            + "".join(cells)
            + "</tr>"
        )

    # Failure-mode telemetry
    fm_rows: List[str] = []
    for arm in arm_order:
        fm = data["failure_modes"][arm]
        fail_reasons = (
            ", ".join(f"{k}: {v}" for k, v in fm["failed_stopped_reasons"].items())
            if fm["failed_stopped_reasons"]
            else "none"
        )
        blocks = (
            ", ".join(f"{k}: {v}" for k, v in fm["gate_blocks_by_gate"].items())
            if fm["gate_blocks_by_gate"]
            else "none"
        )
        repairs = (
            ", ".join(f"{k}: {v}" for k, v in fm["repairs_by_gate"].items())
            if fm["repairs_by_gate"]
            else "none"
        )
        fm_rows.append(
            "<tr>"
            f"<td class='arm-name'>{_esc(arm)}</td>"
            f"<td class='mono'>{fm['live_tasks']}</td>"
            f"<td class='mono'>{fm['failed_tasks']}</td>"
            f"<td class='mono'>{fm['empty_answers']}</td>"
            f"<td class='mono'>{fm['invalid_json']}</td>"
            f"<td class='mono'>{fm['overflowed']}</td>"
            f"<td class='mono'>{_esc(fail_reasons)}</td>"
            f"<td class='mono'>{_esc(blocks)}</td>"
            f"<td class='mono'>{_esc(repairs)}</td>"
            "</tr>"
        )

    # Budget parity table
    bp_rows: List[str] = []
    for arm_bp in bp["arms"]:
        models_str = ", ".join(arm_bp["models"]) if arm_bp["models"] else "—"
        bp_rows.append(
            "<tr>"
            f"<td class='arm-name'>{_esc(arm_bp['arm'])}</td>"
            f"<td class='mono'>{_esc(models_str)}</td>"
            f"<td class='mono'>{_esc(_fmt_opt(arm_bp['total_steps'], 0))}</td>"
            f"<td class='mono'>{_esc(_fmt_opt(arm_bp['total_calls'], 0))}</td>"
            f"<td class='mono'>{_esc(_fmt_opt(arm_bp['total_wall_s'], 1, 's'))}</td>"
            f"<td class='mono'>{arm_bp['overflowed']}</td>"
            f"<td class='mono'>{arm_bp['voided']}</td>"
            "</tr>"
        )

    # Void detail list
    void_html = (
        "<ul class='audit-list'>"
        + "".join(
            f"<li class='mono'>{_esc(v['arm'])}/{_esc(v['task_id'])}: "
            f"recorded task_pass={_esc(v['task_pass'])}, wall={_esc(v['wall_s'])}s — "
            f"{_esc(v['reason'])}</li>"
            for v in data["void_detail"]
        )
        + "</ul>"
        if data["void_detail"]
        else "<p class='muted'>0 attempts voided in this run.</p>"
    )

    # Reconciliation failures
    disc_fail_html = ""
    if verdict["discrepancies"]:
        disc_fail_html = (
            "<div class='alert-danger'><strong>Reconciliation Failures (Fatal):</strong><ul>"
            + "".join(f"<li class='mono'>{_esc(d)}</li>" for d in verdict["discrepancies"])
            + "</ul></div>"
        )

    blind_spots_html = "".join(f"<li>{_esc(b)}</li>" for b in GATE_BLIND_SPOTS)

    top_kpi = (
        f"{_esc(top['arm'])} &middot; {top['pass_at_1']:.0%}"
        if top is not None
        else "—"
    )
    top_sub = (
        f"{top['tasks_passed']}/{top['tasks_scored']} tasks (Wilson LB {top['pass_at_1_lower_95']:.0%})"
        if top is not None
        else "No live attempts"
    )
    test_kpi = (
        f"{_esc(test_leader['arm'])} &middot; {test_leader['splits']['test']['pass_at_1']:.0%}"
        if test_leader is not None
        else "—"
    )
    test_sub = (
        f"{test_leader['splits']['test']['tasks_passed']}/{test_leader['splits']['test']['tasks_scored']} held-out test tasks"
        if test_leader is not None
        else "No test split recorded"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>verdict-bench — APEX Evaluation Scorecard</title>
<style>
  :root {{
    --bg: #0b1020;
    --panel: #121a2f;
    --panel-alt: #18233f;
    --border: #253459;
    --text: #e6edf7;
    --muted: #93a4c3;
    --accent: #38bdf8;
    --pass-bg: rgba(16, 185, 129, 0.16);
    --pass-fg: #34d399;
    --warn-bg: rgba(245, 158, 11, 0.16);
    --warn-fg: #fbbf24;
    --fail-bg: rgba(244, 63, 94, 0.16);
    --fail-fg: #fb7185;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
    line-height: 1.5;
    padding: 28px 20px 56px;
  }}
  .container {{ max-width: 1280px; margin: 0 auto; }}
  .header {{
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: flex-start;
    gap: 16px; padding: 24px; background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; margin-bottom: 20px;
  }}
  .eyebrow {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em; color: var(--accent);
    font-weight: 700; margin-bottom: 4px;
  }}
  h1 {{ margin: 0 0 8px; font-size: 26px; font-weight: 700; letter-spacing: -0.02em; }}
  h2 {{ margin: 0 0 14px; font-size: 17px; font-weight: 600; color: #f8fafc; }}
  h4 {{ margin: 0 0 8px; font-size: 14px; color: var(--accent); }}
  .meta-line {{ font-size: 13px; color: var(--muted); }}
  .meta-line code {{ color: var(--text); background: var(--panel-alt); padding: 2px 6px; border-radius: 4px; }}
  .kpi-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 14px; margin-bottom: 20px;
  }}
  .kpi {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px 18px;
  }}
  .kpi-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
  .kpi-value {{ font-size: 22px; font-weight: 700; margin: 4px 0 2px; }}
  .kpi-sub {{ font-size: 12px; color: var(--muted); }}
  .card {{
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    padding: 20px 22px; margin-bottom: 20px;
  }}
  .table-scroll {{ overflow-x: auto; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{
    padding: 10px 12px; border-bottom: 1px solid var(--border); text-align: left;
    vertical-align: middle;
  }}
  th {{
    font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--muted); background: var(--panel-alt); white-space: nowrap;
  }}
  tr:last-child td {{ border-bottom: none; }}
  .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; font-size: 12.5px; }}
  .muted {{ color: var(--muted); }}
  .arm-name {{ font-weight: 700; color: #f8fafc; white-space: nowrap; }}
  .rank {{ color: var(--accent); font-weight: 700; }}
  .bar-wrap {{
    width: 110px; height: 6px; background: var(--panel-alt); border-radius: 99px;
    overflow: hidden; margin-bottom: 4px;
  }}
  .bar-fill {{ height: 100%; background: linear-gradient(90deg, #38bdf8, #34d399); border-radius: 99px; }}
  .badge {{
    display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px;
    font-weight: 700; letter-spacing: 0.05em; white-space: nowrap;
  }}
  .badge-pass {{ background: var(--pass-bg); color: var(--pass-fg); border: 1px solid rgba(52, 211, 153, 0.35); }}
  .badge-warn {{ background: var(--warn-bg); color: var(--warn-fg); border: 1px solid rgba(251, 191, 36, 0.35); }}
  .badge-fail {{ background: var(--fail-bg); color: var(--fail-fg); border: 1px solid rgba(251, 113, 133, 0.35); }}
  .badge-muted {{ background: var(--panel-alt); color: var(--muted); border: 1px solid var(--border); }}
  .pill {{
    display: inline-block; padding: 2px 7px; border-radius: 4px; font-size: 11px; font-weight: 700;
  }}
  .pill-dev {{ background: rgba(56, 189, 248, 0.14); color: #7dd3fc; }}
  .pill-test {{ background: rgba(168, 85, 247, 0.18); color: #d8b4fe; }}
  .pill-unknown {{ background: var(--panel-alt); color: var(--muted); }}
  .heat-high {{ background: rgba(16, 185, 129, 0.14); color: #6ee7b7; font-weight: 600; }}
  .heat-mid {{ background: rgba(245, 158, 11, 0.14); color: #fcd34d; font-weight: 600; }}
  .heat-low {{ background: rgba(244, 63, 94, 0.14); color: #fda4af; font-weight: 600; }}
  .heat-na {{ color: var(--muted); }}
  .sub {{ font-size: 11px; color: var(--muted); margin-top: 2px; }}
  .disc-grid {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 12px; margin-top: 16px;
  }}
  .disc-card {{
    background: var(--panel-alt); border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 14px;
  }}
  .disc-card ul, .audit-list {{ margin: 6px 0 0; padding-left: 18px; font-size: 12.5px; }}
  .disc-card li, .audit-list li {{ margin-bottom: 4px; }}
  .alert-danger {{
    margin-top: 14px; padding: 12px 14px; border-radius: 8px;
    background: var(--fail-bg); border: 1px solid rgba(251, 113, 133, 0.45);
    color: var(--fail-fg); font-size: 13px;
  }}
  .footer-note {{ font-size: 12px; color: var(--muted); margin-top: 12px; }}
</style>
</head>
<body>
<div class="container">
  <header class="header">
    <div>
      <div class="eyebrow">verdict-bench // APEX Evaluation Scorecard</div>
      <h1>Multi-Arm Benchmark Report</h1>
      <div class="meta-line">
        Rule v{_esc(RULE_VERSION)} &middot; Target <code>{data['target']:.0%}</code> &middot;
        rows <code>{_esc(data['rows_sha256'][:16] or 'none')}</code> &middot;
        gold <code>{_esc(data['gold_sha256'][:16])}</code> &middot;
        split <code>{_esc(data['split_sha256'][:16] or 'none')}</code>
      </div>
      <div class="meta-line" style="margin-top:6px;">Model route(s): {routes_html}</div>
    </div>
    <div style="text-align:right;">
      <div class="eyebrow">Independent Verdict</div>
      <span class="badge {_verdict_class(verdict['verdict'])}" style="font-size:14px;padding:6px 14px;">{_esc(verdict['verdict'])}</span>
    </div>
  </header>

  <section class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Overall Leader</div>
      <div class="kpi-value">{top_kpi}</div>
      <div class="kpi-sub">{_esc(top_sub)}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Held-Out Test Leader</div>
      <div class="kpi-value">{test_kpi}</div>
      <div class="kpi-sub">{_esc(test_sub)}</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Attempts &amp; Voids</div>
      <div class="kpi-value">{data['rows_total']} total &middot; {data['voids']} voided</div>
      <div class="kpi-sub">{len(arm_order)} arm(s) across {len(data['task_matrix'])} task(s)</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Custody &amp; Model Parity</div>
      <div class="kpi-value"><span class="badge {custody_cls}">{custody_label}</span></div>
      <div class="kpi-sub">Distinct models: {len(bp['distinct_models']) or 'unrecorded'} &middot; Overflows: {bp['total_overflowed']}</div>
    </div>
  </section>

  <section class="card">
    <h2>1. Executive Leaderboard</h2>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Rank</th><th>Arm</th><th>Overall Pass@1</th><th>Wilson 95% LB</th>
            <th>Dev Pass@1</th><th>Held-Out Test</th><th>Criteria</th><th>Valid JSON</th>
            <th>Avg Steps</th><th>Avg Calls</th><th>Avg Wall</th><th>Blocks / Repairs</th><th>Verdict</th>
          </tr>
        </thead>
        <tbody>{"".join(lb_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h2>2. Per-Domain Capability Matrix</h2>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Domain</th><th>Tasks</th>{dom_head}</tr></thead>
        <tbody>{"".join(dom_rows)}</tbody>
      </table>
    </div>
  </section>

  {mc_section}

  <section class="card">
    <h2>4. Full Task Outcome Matrix</h2>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Task</th><th>Split</th><th>Domain</th>{task_head}</tr></thead>
        <tbody>{"".join(task_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h2>5. Failure-Mode Telemetry &amp; Gate Activity</h2>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Arm</th><th>Live</th><th>Failed</th><th>Empty</th><th>Invalid JSON</th>
            <th>Overflows</th><th>Failure Stopped Reasons</th><th>Gate Blocks</th><th>Repairs</th>
          </tr>
        </thead>
        <tbody>{"".join(fm_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h2>6. Void Policy, Budget Parity &amp; Cryptographic Custody</h2>
    <div class="table-scroll" style="margin-bottom:14px;">
      <table>
        <thead>
          <tr>
            <th>Arm</th><th>Models</th><th>Total Steps</th><th>Total Calls</th>
            <th>Total Wall (s)</th><th>Overflows</th><th>Voided</th>
          </tr>
        </thead>
        <tbody>{"".join(bp_rows)}</tbody>
      </table>
    </div>
    <h4>Void-and-Rerun Audit ({data['voids']} of {data['rows_total']} voided)</h4>
    {void_html}
    {disc_fail_html}
  </section>

  <section class="card">
    <h2>7. Disclosures &amp; Gate Blind Spots</h2>
    <p class="muted">The gates cannot check:</p>
    <ul class="audit-list">{blind_spots_html}</ul>
    <p class="footer-note">
      Generated deterministically by <code>python -m runner.report</code> from committed artifacts.
      No timestamps; byte-identical on regeneration.
    </p>
  </section>
</div>
</body>
</html>
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="generate the APEX run report")
    parser.add_argument("--rows", type=Path, required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--target", type=float, default=0.70)
    parser.add_argument("--split", type=Path, default=None)
    parser.add_argument("--receipts", type=Path, default=None)
    parser.add_argument("--log", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--html",
        type=Path,
        default=None,
        help="write standalone zero-dependency APEX HTML scorecard here",
    )
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
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(data["text"])
        print(f"report: {args.out}")
    if args.html:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(data["html"])
        print(f"html report: {args.html}")
    if not args.out and not args.html:
        print(data["text"])
    return 0 if data["verdict"]["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
