#!/usr/bin/env python3
# verdict-bench world/grader.py
#
# Code-Graded Criteria Grader.
#
# Every criterion is graded in code — no LLM judge.
# Compares agent output against gold hashes from the withheld answer key.

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# The withheld gold path convention lives in world/paths.py. Insert the repo root so
# this file works both as `python -m world.grader` and as `python3 world/grader.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from world.paths import GOLD_PATH  # noqa: E402


def load_gold() -> Dict[str, Any]:
    return json.loads(GOLD_PATH.read_text())


def grade_task_output(
    task_id: str, agent_output: str, gold: Dict[str, Any]
) -> Dict[str, Any]:
    """Grade one task's agent output against gold."""
    answer = gold["answers"].get(task_id)
    if answer is None:
        return {
            "task_id": task_id,
            "passed": False,
            "reason": "task not in gold answer key",
        }

    expected_hash = answer["gold_hash"]
    actual_hash = hashlib.sha256(agent_output.encode()).hexdigest()

    # Exact match
    if actual_hash == expected_hash:
        return {
            "task_id": task_id,
            "passed": True,
            "reason": "exact hash match",
        }

    # Partial match: check if key substrings from gold answer appear
    gold_answer = answer["gold_answer"]
    if isinstance(gold_answer, dict):
        found_keys = 0
        total_keys = len(gold_answer)
        for key, value in gold_answer.items():
            if str(value) in agent_output:
                found_keys += 1
        if found_keys == total_keys and total_keys > 0:
            return {
                "task_id": task_id,
                "passed": True,
                "reason": f"all {total_keys} gold values present in output",
            }
        elif found_keys > 0:
            return {
                "task_id": task_id,
                "passed": False,
                "reason": f"partial match: {found_keys}/{total_keys} gold values",
                "partial_score": found_keys / total_keys,
            }

    return {
        "task_id": task_id,
        "passed": False,
        "reason": "hash mismatch, no gold values found",
        "expected_hash": expected_hash[:12],
        "actual_hash": actual_hash[:12],
    }


def grade_world(results_path: Path, gold_path: Optional[Path] = None) -> Dict[str, Any]:
    """Grade all task results in a results file."""
    if gold_path is None:
        gold_path = GOLD_PATH
    gold = load_gold()

    results = json.loads(results_path.read_text())
    task_results = results.get("task_results", [])

    graded: List[Dict[str, Any]] = []
    passed = 0
    total = 0

    for tr in task_results:
        task_id = tr["task_id"]
        agent_output = tr.get("agent_output", "")
        grade = grade_task_output(task_id, agent_output, gold)
        graded.append(grade)
        total += 1
        if grade["passed"]:
            passed += 1

    pass_rate = passed / total if total > 0 else 0.0

    return {
        "world_id": results.get("world_id", "unknown"),
        "total_tasks": total,
        "passed": passed,
        "pass_rate": round(pass_rate, 4),
        "pass_at_1": round(pass_rate, 4),  # k=1, so pass@1 = pass rate
        "task_grades": graded,
    }


def write_grade_report(grade: Dict[str, Any], output_path: Path) -> Path:
    lines: List[str] = []
    lines.append("# Grading Report")
    lines.append("")
    lines.append(f"World: {grade['world_id']}")
    lines.append(f"Total tasks: {grade['total_tasks']}")
    lines.append(f"Passed: {grade['passed']}")
    lines.append(f"Pass rate: {grade['pass_rate']:.1%}")
    lines.append(f"Pass@1: {grade['pass_at_1']:.1%}")
    lines.append("")
    lines.append("## Task Grades")
    for g in grade["task_grades"]:
        status = "PASS" if g["passed"] else "FAIL"
        lines.append(f"- [{'x' if g['passed'] else ' '}] {g['task_id']}: {status}")
        if not g["passed"]:
            lines.append(f"  Reason: {g.get('reason', 'unknown')}")
    lines.append("")

    output_path.write_text("\n".join(lines))
    return output_path


if __name__ == "__main__":
    import sys

    results_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("results.json")
    grade = grade_world(results_path)
    rpt_path = write_grade_report(grade, Path("grading_report.md"))
    print(
        f"Pass@1: {grade['pass_at_1']:.1%} ({grade['passed']}/{grade['total_tasks']})"
    )
    print(f"Report written to {rpt_path}")
