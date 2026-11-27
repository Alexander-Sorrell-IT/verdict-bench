#!/usr/bin/env python3
# verdict-bench harness2_adapter/h2_world.py
#
# The Harness²-shaped arm (arm C), ported from the GitHub adapter's SHAPE
# (read-first, E.3): Propose -> Probe -> Compose, one step per task, lessons
# threaded across the tasks of each family, the model as its own executor.
# The method never sees gold, criteria, the grader, or a score.
#
# NOT ported: docker, OpenCode, the external harness2 package - this repo
# executes nothing external. The plumbing is our own: the shared sandbox, the
# command whitelist, the traversal refusal (runner/hypothesis.py), and the
# sealed grader (world/grader.py). Python's convenience: `harness2_adapter`
# is a namespace package, so `import harness2_adapter.h2_world` works from the
# repo root without an __init__.py.

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

BENCH_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR / "runner"))
sys.path.insert(0, str(BENCH_DIR / "world"))

from freeze import FreezeError, enforce_freeze  # noqa: E402
from hypothesis import (  # noqa: E402
    SYSTEM_PROMPT,
    build_sandbox,
    cleanup_sandbox,
    extract_command,
    execute_command,
    relay_client,
)
from operator_mode import task_for_arm  # noqa: E402
from receipts import (  # noqa: E402
    ReceiptChain,
    receipt_run_manifest,
    receipt_task_grade,
    verify_receipt_chain,
)
from world.grader import grade_task_output  # noqa: E402
from world.paths import GOLD_PATH, MANIFEST_PATH  # noqa: E402

ARM_NAME = "C"

PROPOSE_PROMPT = """You are working one task of a family of similar tasks in an unknown
environment. Your family's lessons so far:

{lessons}

TASK: {description}

Output only the next shell command to run - one line, no explanation. Read only
what is inside this sandbox."""

COMPOSE_PROMPT = """You have probed the environment for this task. Compose the final
answer now.

TASK: {description}

What you ran and saw:
{history}

Your family's lessons so far:
{lessons}

Output exactly one line: ANSWER: <the JSON object in exactly the shape the task
description asks for>. Never guess: if the probes did not produce the answer,
answer with what the evidence supports or nothing."""

LESSON_PROMPT = """You finished a task of this family. What did you learn that would help
the next task of the same family? One line, concrete, no answer values.

TASK: {description}
Outcome: your answer was {correct}.
What you ran and saw:
{history}

Output one lesson line, or NONE."""


def _call(client: Any, prompt: str, max_tokens: int = 500) -> str:
    return client.complete(
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
    )


def _parse_answer(decision: str) -> Optional[str]:
    text = decision.replace("```", "").strip()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("ANSWER:"):
            payload = line[len("ANSWER:") :].strip()
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, dict):
                return json.dumps(parsed, sort_keys=True)
    return None


def run_task(
    task: Dict[str, Any],
    client: Any,
    sandbox: Path,
    lessons: List[str],
    probes: int = 3,
) -> Dict[str, Any]:
    """One task, Harness²-shaped: propose, probe, compose."""
    description = task["description"]
    evidence = task.get("evidence_file")
    if evidence:
        description += f"\n\nYour task's evidence file is world_files/{evidence}."
    history: List[str] = []

    for _ in range(probes):
        try:
            reply = _call(
                client,
                PROPOSE_PROMPT.format(
                    lessons="\n".join(lessons) or "(none yet)",
                    description=description,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - a dead model ends the task
            history.append(f"propose failed: {exc}")
            break
        cmd = extract_command(reply)
        if cmd is None:
            history.append(f"no whitelisted command in: {reply[:120]}")
            continue
        result = execute_command(cmd, cwd=sandbox)
        history.append(
            f"$ {cmd}\n  exit={result['exit_code']}\n"
            f"  out={result['stdout'][:600]}\n  err={result['stderr'][:200]}"
        )

    answer = None
    if history:
        try:
            answer = _parse_answer(
                _call(
                    client,
                    COMPOSE_PROMPT.format(
                        description=description,
                        history="\n".join(history),
                        lessons="\n".join(lessons) or "(none yet)",
                    ),
                    max_tokens=2000,
                )
            )
        except Exception:  # noqa: BLE001 - a dead compose ends the task, honestly
            answer = None

    gold_document = json.loads(GOLD_PATH.read_text())
    grade = grade_task_output(task["task_id"], answer or "", gold_document)
    return {
        "arm": ARM_NAME,
        "task_id": task["task_id"],
        "family": task.get("family", "unknown"),
        "rep": 1,
        "task_pass": grade["passed"],
        "criteria_passed": 1 if grade["passed"] else 0,
        "criteria_total": 1,
        "valid_json": answer is not None,
        "answer": answer or "",
        "answer_empty": not answer,
        "model": client.model,
        "base_url": client.base_url,
        "reason": grade.get("reason", ""),
        "history": history,
    }


def run_family(
    family: str,
    tasks: List[Dict[str, Any]],
    client: Any,
    sandbox: Path,
    probes: int = 3,
) -> List[Dict[str, Any]]:
    """The family loop with lessons threaded across its tasks."""
    lessons: List[str] = []
    rows: List[Dict[str, Any]] = []
    for task in tasks:
        row = run_task(task, client, sandbox, lessons, probes=probes)
        try:
            lesson = _call(
                client,
                LESSON_PROMPT.format(
                    description=task["description"],
                    correct="correct" if row["task_pass"] else "wrong",
                    history="\n".join(row["history"]) or "(nothing ran)",
                ),
            ).strip()
        except Exception:  # noqa: BLE001 - a dead lesson just ends the threading
            lesson = ""
        if lesson and lesson.upper() != "NONE":
            lessons.append(lesson.splitlines()[0][:300])
        rows.append(row)
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="the Harness²-shaped arm (C)")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--key-env", default="")
    parser.add_argument("--probes", type=int, default=3)
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument(
        "--out", type=Path, default=BENCH_DIR / "results" / "E3-harness2"
    )
    args = parser.parse_args(argv)

    # The same fixed path as the bench arms: frozen world, evidence naming in
    # the task the arm receives, rows and grades on a receipt chain.
    try:
        enforce_freeze(BENCH_DIR / "spec" / "split.json")
    except FreezeError as exc:
        print(f"RUN REFUSED: {exc}", file=sys.stderr)
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text())
    families = _task_families()
    by_family: Dict[str, List[Dict[str, Any]]] = {}
    for task in manifest["tasks"]:
        task = task_for_arm(task)
        task["family"] = families.get(task["task_id"], "unknown")
        by_family.setdefault(task["family"], []).append(task)

    client = relay_client(
        provider_id=args.provider, model=args.model, key_env=args.key_env
    )
    args.out.mkdir(parents=True, exist_ok=True)
    chain = ReceiptChain(args.out / "receipts.json")
    receipt_run_manifest(
        chain,
        {
            "kind_detail": "h2_world",
            "provider": args.provider or "default",
            "model": client.model,
            "base_url": client.base_url,
            "probes": str(args.probes),
        },
    )
    rows: List[Dict[str, Any]] = []
    sandbox = build_sandbox()
    started = time.time()
    try:
        for family in sorted(by_family):
            print(
                f"[{ARM_NAME}] family {family}: {len(by_family[family])} tasks",
                flush=True,
            )
            family_rows = run_family(
                family, by_family[family], client, sandbox, probes=args.probes
            )
            for row in family_rows:
                print(
                    f"  {ARM_NAME} {row['task_id']}: pass={row['task_pass']} "
                    f"({client.manifest_fields()['calls']} calls so far)",
                    flush=True,
                )
                receipt_task_grade(
                    chain,
                    ARM_NAME,
                    row["task_id"],
                    row["task_pass"],
                    row["reason"],
                )
                rows_file = args.out / "results.jsonl"
                mode = "a" if rows_file.exists() else "w"
                with rows_file.open(mode) as handle:
                    handle.write(
                        json.dumps({k: v for k, v in row.items() if k != "history"})
                        + "\n"
                    )
            rows.extend(family_rows)
    finally:
        cleanup_sandbox(sandbox)

    receipts_verification = verify_receipt_chain(args.out / "receipts.json")
    rows_path = args.out / "results.jsonl"
    passed = sum(1 for r in rows if r["task_pass"])
    summary = {
        "arm": ARM_NAME,
        "model": client.model,
        "base_url": client.base_url,
        "probes": args.probes,
        "receipts_ok": receipts_verification["ok"],
        "model_calls": client.manifest_fields()["calls"],
        "wall_s": round(time.time() - started, 1),
        "passed": passed,
        "total": len(rows),
        "rows": str(rows_path),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if passed == len(rows) else 1


def _task_families() -> Dict[str, str]:
    from world.build_world import generate_tasks

    return {t["task_id"]: t["family"] for t in generate_tasks()}


if __name__ == "__main__":
    raise SystemExit(main())
