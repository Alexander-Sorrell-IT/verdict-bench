#!/usr/bin/env python3
# verdict-bench runner/run_world.py
#
# Main runner for verdict-bench.
#
# Pipeline: discovery → hypothesis → verify → grade
#
# Each task runs through:
#   1. Discovery — map the unknown environment
#   2. Hypothesis — form hypotheses, test, recover when wrong
#   3. Verify — self-verify the deliverable independently
#   4. Grade — code-graded against withheld gold
#
# Usage:
#   python runner/run_world.py --world world_manifest.json --arms AF2
#   python runner/run_world.py --arms A B AF2  (compare arms)

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Add verdict-bench to path
VERDICT_BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VERDICT_BENCH_DIR / "runner"))
sys.path.insert(0, str(VERDICT_BENCH_DIR))

from discovery import run_discovery  # noqa: E402
from hypothesis import (  # noqa: E402
    HypothesisEngine,
    build_sandbox,
    cleanup_sandbox,
    local_client,
    relay_client,
    run_hypothesis_task,
)
from harness.adversary import Adversary, measure_recovery  # noqa: E402
from verify import verify_deliverable, write_verification_report  # noqa: E402
from world.grader import grade_task_output  # noqa: E402
from receipts import (  # noqa: E402
    ReceiptChain,
    verify_hashed_file,
    verify_receipt_chain,
)
from freeze import FreezeError, enforce_freeze  # noqa: E402
from world.gates import check_world, report as gate_report  # noqa: E402
from world.paths import GOLD_FILENAME, PRIVATE_DIRNAME  # noqa: E402


def _task_families() -> Dict[str, str]:
    """task_id -> family, from the generator's PRIVATE list.

    The manifest is public and carries no taxonomy (A.1), so the runner - which
    is harness side, not agent side - supplies it for the record. What the
    runner knows never reaches the arm: it goes in rows and reports only.
    """
    from world.build_world import generate_tasks

    return {t["task_id"]: t["family"] for t in generate_tasks()}


def _load_split(spec_path: Path) -> Dict[str, Any]:
    """The frozen split (B.2). Records dev/test membership per task; a missing
    file means no split, not a default - the run says so and runs everything."""
    if not spec_path.is_file():
        return {"rule": "none", "membership": {}}
    split = json.loads(spec_path.read_text())
    membership = {entry["task_id"]: "dev" for entry in split.get("dev", [])}
    membership.update({entry["task_id"]: "test" for entry in split.get("test", [])})
    return {"rule": split.get("rule", "none"), "membership": membership}


def run_arm(
    world_manifest: Path,
    arm_name: str,
    output_dir: Path,
    split: Dict[str, Any] | None = None,
    only_split: str | None = None,
    client: Any = None,
    adversary: Any = None,
) -> Dict[str, Any]:
    """Run one arm over its split share of the tasks, graded against withheld gold.

    Per-task rows (one JSON object per line, the apex shape) carry the SAME
    fields for every arm, and task_pass is the hash grade - never the
    self-verification's file-exists check, which is the kind of "pass" that
    manufactured the old 25/25. World 2 has one criterion per task (the gold
    hash), so criteria_passed/criteria_total are 1/0 and 1.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(world_manifest.read_text())
    tasks = manifest["tasks"]
    families = _task_families()
    membership = (split or {}).get("membership", {})
    world_dir = VERDICT_BENCH_DIR / "world"
    gold_path = world_dir / PRIVATE_DIRNAME / GOLD_FILENAME
    gold = json.loads(gold_path.read_text())

    task_results: List[Dict[str, Any]] = []
    discovery_path = output_dir / "discovery.md"
    hypothesis_log_path = output_dir / "hypothesis_log.md"
    receipts_path = output_dir / "receipts.json"
    chain = ReceiptChain(receipts_path)

    # The arm reads a SANDBOX: world_files and the manifest copied out of the
    # world dir. Discovery and every command run inside it - the withheld
    # private root stays outside the tree the arm can touch (D.1).
    sandbox = build_sandbox(world_dir)

    # Phase 1: Discovery (once per world, cached) - identical for every arm.
    print(f"[{arm_name}] Phase 1: Environment discovery...")
    discovery_path = run_discovery(sandbox)
    discovery_text = discovery_path.read_text() if discovery_path.exists() else ""
    print(f"[{arm_name}] Discovery written to {discovery_path}")

    # Phase 2-4: Per task — hypothesis → self-verify → grade
    engine = HypothesisEngine(log_path=hypothesis_log_path)

    for i, task in enumerate(tasks):
        task_id = task["task_id"]
        family = families.get(task_id, task.get("family", "unknown"))
        description = task["description"]
        evidence = task.get("evidence_file")
        if evidence:
            # The manifest names the task's evidence file (the plan's design,
            # and the fix for the live run that catted the first file of the
            # right SHAPE): selection must not be a coin flip.
            description += f"\n\nYour task's evidence file is world_files/{evidence}."
        split_member = membership.get(task_id)

        if only_split and split_member != only_split:
            continue

        print(f"[{arm_name}] Task {i+1}/{len(tasks)}: {task_id} ({family})")

        # Hypothesis-driven task solving. The prompt is built from the public
        # description only - identical for every arm (parity is structural).
        start = time.perf_counter()
        answer = run_hypothesis_task(
            task_description=description,
            task_id=task_id,
            engine=engine,
            max_attempts=5,
            discovery_text=discovery_text,
            client=client,
            sandbox=sandbox,
            adversary=adversary,
        )
        elapsed = time.perf_counter() - start

        # Phase 3: Self-verification - recorded as evidence, never as the verdict.
        # In the SANDBOX, not the working tree: the self-verify runs where the
        # arm worked, a tree that provably holds no gold (D.5).
        deliverable_path = sandbox / f"deliverable_{task_id}.txt"
        if answer:
            deliverable_path.write_text(answer)

        if deliverable_path.exists():
            vreport = verify_deliverable(deliverable_path)
            write_verification_report(vreport)
        else:
            vreport = {"overall": "fail", "checks": []}

        # Phase 4: The verdict - code-graded against withheld gold.
        grade = grade_task_output(task_id, answer or "", gold)
        valid_json = False
        if answer:
            try:
                json.loads(answer)
                valid_json = True
            except json.JSONDecodeError:
                valid_json = False

        task_results.append(
            {
                "arm": arm_name,
                "task_id": task_id,
                "family": family,
                "rep": 1,
                "model": getattr(client, "model", None),
                "base_url": getattr(client, "base_url", None),
                "task_pass": grade["passed"],
                "criteria_passed": 1 if grade["passed"] else 0,
                "criteria_total": 1,
                "valid_json": valid_json,
                "wall_s": round(elapsed, 2),
                "answer": answer or "",
                "answer_empty": not bool(answer and answer.strip()),
                "split": split_member,
                "adversary": adversary.event_for(task_id) if adversary else None,
                "recovery": (
                    measure_recovery(engine.cycles[-1], adversary.event_for(task_id))
                    if adversary and adversary.event_for(task_id) and engine.cycles
                    else None
                ),
                "self_verify": vreport.get("overall", "fail"),
                "reason": grade.get("reason", ""),
            }
        )

        # Clean up deliverable
        if deliverable_path.exists():
            deliverable_path.unlink()

        # The run log is written per task and RECEIPTED per task (D.6): the
        # chain proves the receipts, verify_hashed_file proves the log - an edit
        # after the run, human or otherwise, fails the audit.
        log_path = engine.write_log()
        chain.append(
            {
                "kind": "hypothesis_log",
                "task_id": task_id,
                "path": str(log_path),
                "sha256": hashlib.sha256(log_path.read_bytes()).hexdigest(),
            }
        )

    cleanup_sandbox(sandbox)
    receipts_verification = verify_receipt_chain(receipts_path)

    # One row per task, appended - the same shape for every arm, so stats.py
    # reads runs from any arm without conversion.
    rows_path = output_dir / "results.jsonl"
    with rows_path.open("a") as handle:
        for row in task_results:
            handle.write(json.dumps(row) + "\n")

    results = {
        "arm": arm_name,
        "world_id": manifest["world_id"],
        "seed": manifest["seed"],
        "split_rule": (split or {}).get("rule", "none"),
        "total_tasks": len(task_results),
        "passed": sum(1 for r in task_results if r["task_pass"]),
        "log_audit": verify_hashed_file(receipts_path, hypothesis_log_path),
        "receipts": receipts_verification,
        "task_results": task_results,
        "recovery_rate": engine.recovery_rate(),
    }
    results_path = output_dir / f"results_{arm_name}.json"
    results_path.write_text(json.dumps(results, indent=2))

    return results


def compare_arms(results_dir: Path) -> None:
    """Compare the graded rows and write a comparison report."""
    rows: List[Dict[str, Any]] = []
    rows_path = results_dir / "results.jsonl"
    if rows_path.is_file():
        rows = [
            json.loads(line)
            for line in rows_path.read_text().splitlines()
            if line.strip()
        ]

    recovery: Dict[str, float] = {}
    for results_file in sorted(results_dir.glob("results_*.json")):
        arm_data = json.loads(results_file.read_text())
        recovery[arm_data["arm"]] = arm_data.get("recovery_rate", 0.0)

    per_arm: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        per_arm.setdefault(row["arm"], []).append(row)

    lines: List[str] = []
    lines.append("# Arm Comparison Report")
    lines.append("")
    lines.append("| Arm | Tasks | Passed | Pass@1 | Recovery (legacy) |")
    lines.append("|-----|-------|--------|--------|-------------------|")
    for arm_name in sorted(per_arm):
        arm_rows = per_arm[arm_name]
        passed = sum(1 for r in arm_rows if r["task_pass"])
        total = len(arm_rows)
        rate = passed / total if total else 0.0
        lines.append(
            f"| {arm_name} | {total} | {passed} | {rate:.1%} | "
            f"{recovery.get(arm_name, 0.0):.1%} |"
        )
    lines.append("")

    (results_dir / "comparison_report.md").write_text("\n".join(lines))
    print("\nComparison written to", results_dir / "comparison_report.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run verdict-bench arms")
    parser.add_argument(
        "--world",
        default="world/world_manifest.json",
        help="Path to world manifest JSON",
    )
    parser.add_argument(
        "--arms", nargs="+", default=["AF2"], help="Arms to run (e.g., A B AF2)"
    )
    parser.add_argument(
        "--output-dir", default="results", help="Output directory for results"
    )
    parser.add_argument(
        "--spec-split",
        default=str(VERDICT_BENCH_DIR / "spec" / "split.json"),
        help="Frozen dev/test split (B.2); rows record membership",
    )
    parser.add_argument(
        "--only-split",
        choices=["dev", "test"],
        default=None,
        help="Run only this split member's tasks",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="debug route: local Ollama instead of the graded relay model",
    )
    parser.add_argument(
        "--adversary-seed",
        type=int,
        default=None,
        help="inject a deterministic contradiction at attempt 2 of every task "
        "(the recovery measurement; omit for the clean control run)",
    )
    parser.add_argument(
        "--build-world", action="store_true", help="Build the world before running"
    )
    parser.add_argument("--seed", type=int, default=20261120, help="World seed")
    parser.add_argument("--num-tasks", type=int, default=25, help="Number of tasks")
    args = parser.parse_args()

    world_path = Path(args.world)
    output_dir = Path(args.output_dir)

    # Build world if requested
    if args.build_world:
        from world.build_world import build_world

        build_world(seed=args.seed, num_tasks=args.num_tasks)

    if not world_path.exists():
        print(f"World manifest not found: {world_path}")
        print("Run with --build-world to generate it first.")
        sys.exit(1)

    # The hard rule, before any arm runs: a world that cannot be verified in
    # code does not run. The world dir holds the withheld private root, so the
    # sandbox the gates check is the data dir the arm reads (world_files) and
    # the public-tree scan covers the manifest beside it.
    world_dir = world_path.resolve().parent
    results, blocking = check_world(
        world_dir,
        world_dir / "world_files",
        world_dir / PRIVATE_DIRNAME / GOLD_FILENAME,
    )
    print(gate_report(results, blocking))
    if blocking:
        print(f"RUN REFUSED: world gates blocking: {', '.join(blocking)}")
        sys.exit(1)

    # E.1: the world and the split are frozen before any arm runs. A change
    # under the run aborts it - not a warning, an abort.
    try:
        freeze_outcome = enforce_freeze(Path(args.spec_split))
    except FreezeError as exc:
        print(f"RUN REFUSED: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Freeze: {freeze_outcome['status']}")

    # Run each arm - same split, same world, same prompt path (parity is
    # structural); the only difference between arms is the arm itself. ONE
    # client for every arm: the graded relay route unless --local is debugging.
    split = _load_split(Path(args.spec_split))
    client = local_client() if args.local else relay_client()
    adversary = Adversary(args.adversary_seed) if args.adversary_seed else None
    print(
        f"Model route: {client.model} via {client.base_url}"
        f"{' (LOCAL DEBUG)' if args.local else ''}"
    )
    if adversary is not None:
        print(
            f"Adversary: deterministic contradiction at attempt "
            f"{adversary.at_attempt + 1} of every task (seed {adversary.seed})"
        )
    all_results: List[Dict[str, Any]] = []
    for arm in args.arms:
        print(f"\n{'='*60}")
        print(f"Running arm: {arm}")
        print(f"{'='*60}")
        result = run_arm(
            world_path,
            arm,
            output_dir,
            split=split,
            only_split=args.only_split,
            client=client,
            adversary=adversary,
        )
        all_results.append(result)

    # Compare arms
    if len(args.arms) > 1:
        compare_arms(output_dir)

    # Print summary - GRADED pass counts. The self-verification "pass" (file
    # exists, non-empty, parses) is recorded per row as self_verify evidence and
    # never counted here: that is exactly how 25/25 was manufactured.
    print(f"\n{'='*60}")
    print("SUMMARY (hash-graded against withheld gold)")
    print(f"{'='*60}")
    for result in all_results:
        injected = [r for r in result["task_results"] if r.get("adversary")]
        recovered = sum(
            1 for r in injected if (r.get("recovery") or {}).get("recovered")
        )
        line = (
            f"  {result['arm']}: {result['passed']}/"
            f"{result['total_tasks']} passed (hash-graded)"
        )
        if injected:
            line += (
                f" | recovery {recovered}/{len(injected)} measured after "
                "contradiction"
            )
        line += f" | legacy recovery_rate {result['recovery_rate']:.0%}"
        print(line)


if __name__ == "__main__":
    main()
