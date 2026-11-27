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

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR / "runner"))
sys.path.insert(0, str(BENCH_DIR / "world"))

from bench import load_arms_spec  # noqa: E402
from freeze import FreezeError, enforce_freeze  # noqa: E402
from harness.budget import Budget  # noqa: E402
from harness.minimal_arm import _extract_answer  # noqa: E402
from hypothesis import (  # noqa: E402
    build_sandbox,
    cleanup_sandbox,
    extract_command,
    execute_command,
    relay_client,
)
from operator_mode import _description_with_evidence, _split_membership  # noqa: E402
from receipts import (  # noqa: E402
    ReceiptChain,
    receipt_run_manifest,
    receipt_task_grade,
    verify_receipt_chain,
)
from world.grader import grade_task_output  # noqa: E402
from world.paths import GOLD_PATH, MANIFEST_PATH  # noqa: E402

ARM_NAME = "C"

PROPOSE_SYSTEM_PROMPT = (
    "You are an agent probing an unknown sandbox environment using shell commands. "
    "Output ONLY the next shell command to run inside this sandbox - one line, "
    "no explanation. Always run commands to inspect files and compute values."
)

COMPOSE_SYSTEM_PROMPT = (
    "You are an agent composing the final structured JSON answer from your probe "
    "observations. Reply with exactly one line: ANSWER: <json object>. "
    "Never invent values that are not supported by the probe output."
)

LESSON_SYSTEM_PROMPT = (
    "You summarize concise, concrete operational lessons from completed probes "
    "without repeating any specific task answer values. Reply with one short line "
    "or NONE."
)

PROPOSE_PROMPT = """You are working one task in an unknown sandbox environment.
Your operational lessons so far:

{lessons}

TASK: {description}

Previous probes and outputs for THIS task:
{history}

Output only the next shell command to run inside this sandbox - one line, no
explanation. Do not repeat a command whose output you already have."""

COMPOSE_PROMPT = """You have probed the environment for this task. Compose the final
answer now.

TASK: {description}

What you ran and saw:
{history}

Your operational lessons so far:
{lessons}

Output exactly one line: ANSWER: <the JSON object in exactly the shape the task
description asks for>. Never guess: if the probes did not produce the answer,
answer with what the evidence supports or nothing."""

LESSON_PROMPT = """You finished a task. What did you learn? State one concrete operational lesson
that would help on future unknown-environment tasks (one line, general, no task
answer values).

TASK: {description}
Outcome: your answer was {correct}.
What you ran and saw:
{history}

Output one lesson line, or NONE."""


def _call(
    client: Any,
    prompt: str,
    max_tokens: int = 2048,
    system_prompt: str = PROPOSE_SYSTEM_PROMPT,
) -> str:
    return client.complete(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
    )


def _parse_answer(decision: str) -> Optional[str]:
    parsed = _extract_answer(decision)
    if parsed is None:
        return None
    try:
        obj = json.loads(parsed)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):
        return json.dumps(obj, sort_keys=True)
    return None


def run_task(
    task: Dict[str, Any],
    client: Any,
    sandbox: Path,
    lessons: List[str],
    probes: int = 3,
    budget: Optional[Budget] = None,
    split_member: str = "unknown",
) -> Dict[str, Any]:
    """One task, Harness²-shaped: propose (with history) -> probe -> compose."""
    description = _description_with_evidence(task)
    history: List[str] = []
    started = time.perf_counter()
    start_calls = int(getattr(client, "call_count", 0))
    steps = 0
    transport_dead = False
    stopped_reason = "incomplete"

    for _ in range(probes):
        if budget is not None and steps >= budget.max_steps:
            stopped_reason = "budget_steps_exhausted"
            break
        steps += 1
        try:
            reply = _call(
                client,
                PROPOSE_PROMPT.format(
                    lessons="\n".join(lessons[-8:]) or "(none yet)",
                    description=description,
                    history="\n".join(history) or "(none yet)",
                ),
                system_prompt=PROPOSE_SYSTEM_PROMPT,
            )
        except Exception as exc:  # noqa: BLE001 - a dead model ends the task
            error_kind = getattr(exc, "error_kind", "")
            if error_kind in ("transport", "http"):
                transport_dead = True
                stopped_reason = f"transport_error: {error_kind}"
            else:
                stopped_reason = f"model_error: {type(exc).__name__}"
            history.append(f"propose failed: {exc}")
            break
        cmd = extract_command(reply)
        if cmd is None:
            # If the model already returned a valid JSON answer after prior probes, stop probing early.
            early = _parse_answer(reply)
            if early is not None and history:
                history.append(f"early_answer: {early}")
                break
            stopped_reason = "unparseable_reply"
            history.append(f"no whitelisted command in: {reply[:120]}")
            continue
        result = execute_command(cmd, cwd=sandbox)
        history.append(
            f"$ {cmd}\n  exit={result['exit_code']}\n"
            f"  out={result['stdout'][:600]}\n  err={result['stderr'][:200]}"
        )

    answer: Optional[str] = None
    if history and not transport_dead:
        steps += 1
        try:
            compose_reply = _call(
                client,
                COMPOSE_PROMPT.format(
                    description=description,
                    history="\n".join(history),
                    lessons="\n".join(lessons[-8:]) or "(none yet)",
                ),
                max_tokens=2048,
                system_prompt=COMPOSE_SYSTEM_PROMPT,
            )
            answer = _parse_answer(compose_reply)
            if answer is not None:
                stopped_reason = "answered"
            else:
                stopped_reason = "unparseable_compose"
        except Exception as exc:  # noqa: BLE001 - a dead compose ends the task, honestly
            error_kind = getattr(exc, "error_kind", "")
            if error_kind in ("transport", "http"):
                transport_dead = True
                stopped_reason = f"transport_error: {error_kind}"
            else:
                stopped_reason = f"model_error: {type(exc).__name__}"
            answer = None

    wall_s = round(time.perf_counter() - started, 3)
    model_calls = max(0, int(getattr(client, "call_count", start_calls)) - start_calls)
    overflowed = False
    if budget is not None:
        overflowed = (
            steps > budget.max_steps
            or model_calls > budget.max_model_calls
            or wall_s > budget.max_wall_seconds
        )

    gold_document = json.loads(GOLD_PATH.read_text())
    grade = grade_task_output(task["task_id"], answer or "", gold_document)
    return {
        "arm": ARM_NAME,
        "task_id": task["task_id"],
        "family": task.get("family", "unknown"),
        "split": split_member,
        "rep": 1,
        "task_pass": grade["passed"],
        "criteria_passed": 1 if grade["passed"] else 0,
        "criteria_total": 1,
        "valid_json": answer is not None,
        "answer": answer or "",
        "answer_empty": not answer,
        "steps": steps,
        "model_calls": model_calls,
        "wall_s": wall_s,
        "overflowed": overflowed,
        "stopped_reason": stopped_reason,
        "transport_dead": transport_dead,
        "gate_blocks": [],
        "repairs": [],
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
    budget: Optional[Budget] = None,
    lessons: Optional[List[str]] = None,
    learn_lessons: bool = True,
    splits: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Run a sequence of tasks, optionally distilling lessons (dev-split only in APEX)."""
    active_lessons: List[str] = list(lessons) if lessons is not None else []
    rows: List[Dict[str, Any]] = []
    for task in tasks:
        split_member = (splits or {}).get(task["task_id"], "unknown")
        row = run_task(
            task,
            client,
            sandbox,
            active_lessons,
            probes=probes,
            budget=budget,
            split_member=split_member,
        )
        if learn_lessons and not row["transport_dead"]:
            try:
                lesson = _call(
                    client,
                    LESSON_PROMPT.format(
                        description=_description_with_evidence(task),
                        correct="correct" if row["task_pass"] else "wrong",
                        history="\n".join(row["history"]) or "(nothing ran)",
                    ),
                    system_prompt=LESSON_SYSTEM_PROMPT,
                ).strip()
            except Exception:  # noqa: BLE001 - a dead lesson just ends the threading
                lesson = ""
            if lesson and lesson.upper() != "NONE":
                active_lessons.append(lesson.splitlines()[0][:300])
        rows.append(row)
    if lessons is not None:
        lessons[:] = active_lessons
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="the Harness²-shaped arm (C)")
    parser.add_argument("--provider", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--key-env", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--spec", type=Path, default=BENCH_DIR / "spec" / "arms.json")
    parser.add_argument("--probes", type=int, default=3)
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument(
        "--out", type=Path, default=BENCH_DIR / "results" / "E3-harness2"
    )
    args = parser.parse_args(argv)

    # The same fixed path as the bench arms: frozen world, shared spec/arms.json
    # Budget object, evidence naming once, APEX dev->test split hygiene (lessons
    # learned on dev only, frozen on held-out test with no family grouping).
    try:
        enforce_freeze(BENCH_DIR / "spec" / "split.json")
    except FreezeError as exc:
        print(f"RUN REFUSED: {exc}", file=sys.stderr)
        return 1

    spec = load_arms_spec(args.spec)
    budget = Budget(**spec["budget"])
    manifest = json.loads(MANIFEST_PATH.read_text())
    families = _task_families()
    splits = _split_membership(BENCH_DIR / "spec" / "split.json")

    dev_tasks: List[Dict[str, Any]] = []
    test_tasks: List[Dict[str, Any]] = []
    for raw_task in manifest["tasks"]:
        task = dict(raw_task)
        task["family"] = families.get(task["task_id"], "unknown")
        if splits.get(task["task_id"]) == "test":
            test_tasks.append(task)
        else:
            dev_tasks.append(task)

    client = relay_client(
        provider_id=args.provider,
        model=args.model,
        key_env=args.key_env,
        base_url=args.base_url,
    )
    args.out.mkdir(parents=True, exist_ok=True)
    rows_file = args.out / "results.jsonl"
    if rows_file.exists():
        rows_file.unlink()
    receipts_file = args.out / "receipts.json"
    if receipts_file.exists():
        receipts_file.unlink()

    chain = ReceiptChain(receipts_file)
    receipt_run_manifest(
        chain,
        {
            "kind_detail": "h2_world",
            "provider": args.provider or "default",
            "model": client.model,
            "base_url": client.base_url,
            "budget": json.dumps(vars(budget), sort_keys=True),
            "probes": str(args.probes),
        },
    )
    rows: List[Dict[str, Any]] = []
    dev_lessons: List[str] = []
    sandbox = build_sandbox()
    started = time.time()
    try:
        print(f"[{ARM_NAME}] APEX dev phase: {len(dev_tasks)} tasks (learning lessons)", flush=True)
        dev_rows = run_family(
            "dev",
            dev_tasks,
            client,
            sandbox,
            probes=args.probes,
            budget=budget,
            lessons=dev_lessons,
            learn_lessons=True,
            splits=splits,
        )
        print(
            f"[{ARM_NAME}] APEX test phase: {len(test_tasks)} held-out tasks "
            f"(lessons frozen: {len(dev_lessons)}, blind order)",
            flush=True,
        )
        test_rows = run_family(
            "test",
            test_tasks,
            client,
            sandbox,
            probes=args.probes,
            budget=budget,
            lessons=list(dev_lessons),
            learn_lessons=False,
            splits=splits,
        )
        ordered_rows = sorted(dev_rows + test_rows, key=lambda r: r["task_id"])
        for row in ordered_rows:
            print(
                f"  {ARM_NAME} {row['task_id']} [{row['split']}]: pass={row['task_pass']} "
                f"reason={row['stopped_reason']} calls={row['model_calls']}",
                flush=True,
            )
            receipt_task_grade(
                chain,
                ARM_NAME,
                row["task_id"],
                row["task_pass"],
                row["reason"],
            )
            with rows_file.open("a") as handle:
                handle.write(
                    json.dumps(
                        {k: v for k, v in row.items() if k != "history"},
                        sort_keys=True,
                    )
                    + "\n"
                )
        rows.extend(ordered_rows)
    finally:
        cleanup_sandbox(sandbox)

    receipts_verification = verify_receipt_chain(receipts_file)
    passed = sum(1 for r in rows if r["task_pass"])
    dev_passed = sum(1 for r in rows if r["split"] == "dev" and r["task_pass"])
    test_passed = sum(1 for r in rows if r["split"] == "test" and r["task_pass"])
    summary = {
        "arm": ARM_NAME,
        "model": client.model,
        "base_url": client.base_url,
        "budget": vars(budget),
        "probes": args.probes,
        "dev_lessons_count": len(dev_lessons),
        "receipts_ok": receipts_verification["ok"],
        "model_calls": client.manifest_fields()["calls"],
        "wall_s": round(time.time() - started, 1),
        "passed": passed,
        "total": len(rows),
        "dev_passed": dev_passed,
        "dev_total": len(dev_tasks),
        "test_passed": test_passed,
        "test_total": len(test_tasks),
        "rows": str(rows_file),
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if passed == len(rows) else 1


def _task_families() -> Dict[str, str]:
    from world.build_world import generate_tasks

    return {t["task_id"]: t["family"] for t in generate_tasks()}


if __name__ == "__main__":
    raise SystemExit(main())
