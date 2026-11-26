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
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

# Add verdict-bench to path
VERDICT_BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VERDICT_BENCH_DIR / "runner"))
sys.path.insert(0, str(VERDICT_BENCH_DIR / "world"))
sys.path.insert(0, str(VERDICT_BENCH_DIR))

from discovery import run_discovery  # noqa: E402
from hypothesis import HypothesisEngine, run_hypothesis_task  # noqa: E402
from verify import verify_deliverable, write_verification_report  # noqa: E402
from grader import grade_world  # noqa: E402
from world.gates import check_world, report as gate_report  # noqa: E402
from world.paths import GOLD_FILENAME, PRIVATE_DIRNAME  # noqa: E402


def run_arm(world_manifest: Path, arm_name: str, output_dir: Path) -> Dict[str, Any]:
    """Run one arm of the benchmark on all tasks."""
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(world_manifest.read_text())
    tasks = manifest["tasks"]

    task_results: List[Dict[str, Any]] = []
    discovery_path = output_dir / "discovery.md"
    hypothesis_log_path = output_dir / "hypothesis_log.md"

    # Phase 1: Discovery (once per world, cached)
    # Run from the world directory where world_files exists
    world_dir = VERDICT_BENCH_DIR / "world"
    print(f"[{arm_name}] Phase 1: Environment discovery...")
    discovery_path = run_discovery(world_dir)
    discovery_text = discovery_path.read_text() if discovery_path.exists() else ""
    print(f"[{arm_name}] Discovery written to {discovery_path}")

    # Phase 2-4: Per task — hypothesis → verify → grade
    engine = HypothesisEngine(log_path=hypothesis_log_path)

    for i, task in enumerate(tasks):
        task_id = task["task_id"]
        family = task["family"]
        description = task["description"]

        print(f"[{arm_name}] Task {i+1}/{len(tasks)}: {task_id} ({family})")

        # Hypothesis-driven task solving
        start = time.perf_counter()
        answer = run_hypothesis_task(
            task_description=description,
            task_id=task_id,
            engine=engine,
            max_attempts=5,
            discovery_text=discovery_text,
        )
        elapsed = time.perf_counter() - start

        # Phase 3: Self-verification
        deliverable_path = Path(f"deliverable_{task_id}.txt")
        if answer:
            deliverable_path.write_text(answer)

        if deliverable_path.exists():
            vreport = verify_deliverable(deliverable_path)
            write_verification_report(vreport)
        else:
            vreport = {"overall": "fail", "checks": []}

        task_results.append(
            {
                "task_id": task_id,
                "family": family,
                "arm": arm_name,
                "agent_output": answer or "",
                "elapsed_seconds": round(elapsed, 2),
                "verification": vreport.get("overall", "fail"),
                "timestamp": engine.cycles[-1].created_at if engine.cycles else "",
            }
        )

        # Clean up deliverable
        if deliverable_path.exists():
            deliverable_path.unlink()

    # Write hypothesis log
    engine.write_log()

    # Write results
    results = {
        "arm": arm_name,
        "world_id": manifest["world_id"],
        "seed": manifest["seed"],
        "total_tasks": len(tasks),
        "task_results": task_results,
        "recovery_rate": engine.recovery_rate(),
    }
    results_path = output_dir / f"results_{arm_name}.json"
    results_path.write_text(json.dumps(results, indent=2))

    return results


def compare_arms(results_dir: Path) -> None:
    """Grade all arm results and write a comparison report."""
    grade_reports: List[Dict[str, Any]] = []

    for results_file in sorted(results_dir.glob("results_*.json")):
        results = json.loads(results_file.read_text())
        arm_name = results["arm"]

        # Grade against gold
        grade = grade_world(results_file)
        grade["arm"] = arm_name
        grade_reports.append(grade)

    # Write comparison
    lines: List[str] = []
    lines.append("# Arm Comparison Report")
    lines.append("")
    lines.append("| Arm | Tasks | Passed | Pass@1 | Recovery Rate |")
    lines.append("|-----|-------|--------|--------|---------------|")
    for g in grade_reports:
        arm_result = next(
            (
                r
                for r in [
                    json.loads(f.read_text())
                    for f in results_dir.glob("results_*.json")
                ]
                if r["arm"] == g["arm"]
            ),
            None,
        )
        recovery = arm_result.get("recovery_rate", 0.0) if arm_result else 0.0
        lines.append(
            f"| {g['arm']} | {g['total_tasks']} | {g['passed']} | "
            f"{g['pass_at_1']:.1%} | {recovery:.1%} |"
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

    # Run each arm
    all_results: List[Dict[str, Any]] = []
    for arm in args.arms:
        print(f"\n{'='*60}")
        print(f"Running arm: {arm}")
        print(f"{'='*60}")
        result = run_arm(world_path, arm, output_dir)
        all_results.append(result)

    # Compare arms
    if len(args.arms) > 1:
        compare_arms(output_dir)

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for result in all_results:
        passed = sum(
            1 for tr in result["task_results"] if tr.get("verification") == "pass"
        )
        print(
            f"  {result['arm']}: {passed}/{result['total_tasks']} "
            f"verified ({result['recovery_rate']:.0%} recovery rate)"
        )


if __name__ == "__main__":
    main()
