#!/usr/bin/env python3
# verdict-bench runner/operator.py
#
# Operator mode (G): any OpenAI-compatible key, any model, both arms, one
# budget, independent grade, receipts written. Reuses the agent-colosseum
# model-town pattern (PLAN_2026-11-27 §8).
#
#   python -m runner.operator --provider nvidia --model nvidia/nemotron-3-ultra-550b-a55b
#   python -m runner.operator --sweep provider:model,provider:model,... [--execute]
#
# The invariant (G.2): the spec must name ONE model+provider for every arm, or
# the run is refused. Parity is structural - operator mode builds one client
# per config and both arms receive the same object.
#
# --via-relay starts the shared relay for you (subprocess, terminated on exit):
# the key resolves inside the relay and never reaches an arm.

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR / "runner"))
sys.path.insert(0, str(BENCH_DIR / "harness"))

import bench  # noqa: E402
from bench import (  # noqa: E402
    BenchmarkError,
    build_client,
    enforce_world_gates,
    load_arms_spec,
    resolve_runner,
    run_arm,
)
from freeze import FreezeError, enforce_freeze  # noqa: E402
from harness.keys import load_providers  # noqa: E402
from receipts import (  # noqa: E402
    ReceiptChain,
    receipt_run_manifest,
    receipt_task_grade,
    verify_receipt_chain,
)
from score.independent_grader import grade_run  # noqa: E402


class RelayProcess:
    """The shared relay as a subprocess, started and stopped with the run."""

    def __init__(self, upstream: str, port: int = 8787) -> None:
        self.upstream = upstream
        self.port = port
        self.proc: Optional[subprocess.Popen] = None

    def __enter__(self) -> str:
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "runner.relay",
                "--upstream",
                self.upstream,
                "--port",
                str(self.port),
            ],
            cwd=str(BENCH_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 20
        import socket

        while time.time() < deadline:
            with socket.socket() as sock:
                sock.settimeout(1)
                if sock.connect_ex(("127.0.0.1", self.port)) == 0:
                    return f"http://127.0.0.1:{self.port}/v1"
            time.sleep(0.5)
        raise BenchmarkError(f"relay did not come up on :{self.port}")

    def __exit__(self, *exc: Any) -> None:
        if self.proc is not None:
            self.proc.terminate()
            self.proc.wait(timeout=10)


def parse_config(text: str) -> Tuple[str, str]:
    """ "provider:model" or bare "provider" (the provider's default model)."""
    provider, _, model = text.partition(":")
    if not provider:
        raise BenchmarkError(f"config must be 'provider[:model]': {text!r}")
    return provider, model


def run_config(
    provider: str,
    model: str,
    spec_path: Path,
    manifest_path: Path,
    gold_path: Path,
    sandbox: Path,
    out: Path,
    target: float = 0.70,
    via_relay: bool = False,
    execute: bool = True,
    freeze_record: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run both arms at one budget under one model route, grade independently,
    receipt everything. Returns the verdict and the artifact paths.

    freeze_record defaults to the committed spec/frozen_run.json; a caller
    running a DIFFERENT world (a smoke slice) passes its own record, so the
    freeze still means something - it just freezes against what this run is
    actually staged on.
    """
    spec = load_arms_spec(spec_path)
    providers = load_providers(BENCH_DIR / "spec" / "providers.json")
    if provider not in providers:
        raise BenchmarkError(f"unknown provider {provider!r}: {sorted(providers)}")
    base_url = providers[provider].base_url
    client_kwargs: Dict[str, Any] = {}
    if via_relay:
        with RelayProcess(base_url) as relay_url:
            client_kwargs["base_url"] = relay_url
            return _run_once(
                provider,
                model,
                spec,
                manifest_path,
                gold_path,
                sandbox,
                out,
                target,
                client_kwargs,
                execute,
                freeze_record,
            )
    return _run_once(
        provider,
        model,
        spec,
        manifest_path,
        gold_path,
        sandbox,
        out,
        target,
        client_kwargs,
        execute,
        freeze_record,
    )


def _retry_write(operation: Any, what: str, attempts: int = 5) -> Any:
    """One write, retried through a transient disk error.

    An external drive hiccup cost this run 12 minutes once: the EIO arrived
    mid-flush and took the in-memory arm with it. The disk is usually back
    within seconds, so the write waits for it instead of the run dying.
    """
    delay = 1.0
    last_error: Optional[OSError] = None
    for attempt in range(attempts):
        try:
            return operation()
        except OSError as exc:
            last_error = exc
            print(
                f"  [disk] {what} failed ({exc}); retry {attempt + 1}/{attempts}",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
    raise last_error  # type: ignore[misc]


def prompt_for(task: Dict[str, Any]) -> str:
    """The prompt every arm sees for a task.

    The manifest names each task's evidence file (the plan's design, and the
    fix for the live run that catted the first file of the right shape). The
    bench path must deliver it exactly as run_world does: without it an arm
    sees 25 identical-description tasks and 25 files with no way to correlate
    them, explores for confirmation it can never get, and scores zero while
    having already read the right file.
    """
    description = task["description"]
    evidence = task.get("evidence_file")
    if evidence:
        description += f"\n\nYour task's evidence file is world_files/{evidence}."
    return description


def task_for_arm(task: Dict[str, Any]) -> Dict[str, Any]:
    """The task as the arm receives it: the description the prompt is built
    from, with the evidence naming included. The manifest is public, so every
    field of it may reach the model - the withheld parts are gold and rules,
    not names."""
    augmented = dict(task)
    augmented["description"] = prompt_for(task)
    return augmented


def _run_once(
    provider: str,
    model: str,
    spec: Dict[str, Any],
    manifest_path: Path,
    gold_path: Path,
    sandbox: Path,
    out: Path,
    target: float,
    client_kwargs: Dict[str, Any],
    execute: bool,
    freeze_record: Optional[Path] = None,
) -> Dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    rows_path = out / "results.jsonl"
    plan = {
        "provider": provider,
        "model": model or "(provider default)",
        "out": str(out),
        "rows": str(rows_path),
        "receipts": str(out / "receipts.json"),
        "arms": sorted(spec["arms"]),
    }
    if not execute:
        return {"status": "planned", "plan": plan}

    client = build_client(
        BENCH_DIR / "spec" / "providers.json", provider, model, **client_kwargs
    )
    manifest = json.loads(Path(manifest_path).read_text())
    # The arms receive the evidence naming in their task: without it they see
    # 25 identical-description tasks and 25 files, read several, find
    # conflicting values, and cannot decide which is theirs. The first E.2 run
    # scored 0/25 exactly this way - the arm had already catted the right file
    # and the right value twice before its attempts ran out.
    manifest = dict(manifest)
    manifest["tasks"] = [task_for_arm(t) for t in manifest["tasks"]]
    gold = json.loads(Path(gold_path).read_text())
    answers = gold.get("answers", gold)
    budget = bench.Budget(**spec["budget"])

    enforce_world_gates(Path(manifest_path).resolve().parent, sandbox, Path(gold_path))
    enforce_freeze(
        BENCH_DIR / "spec" / "split.json",
        record_path=freeze_record or (BENCH_DIR / "spec" / "frozen_run.json"),
    )

    chain = ReceiptChain(out / "receipts.json")
    receipt_run_manifest(
        chain,
        {
            "provider": provider,
            "model": client.model,
            "base_url": client.base_url,
            "budget": json.dumps(vars(budget), sort_keys=True),
            "manifest_sha256": bench.hashlib.sha256(
                Path(manifest_path).read_bytes()
            ).hexdigest(),
            "gold_sha256": bench.hashlib.sha256(
                Path(gold_path).read_bytes()
            ).hexdigest(),
        },
    )

    rows: List[Dict[str, Any]] = []
    # Rows are written per task, not at the end: a run that is killed mid-way
    # (a timeout, a Ctrl-C) leaves its evidence on disk, the same way the
    # receipt chain already does.
    with _retry_write(lambda: rows_path.open("w"), "open rows") as rows_handle:
        for arm_name, entrypoint in spec["arms"].items():
            runner = resolve_runner(entrypoint)
            report = run_arm(
                arm_name,
                runner,
                manifest,
                answers,
                budget,
                sandbox,
                prompt_for,
                client,
            )
            for attempt, grade in zip(report.attempts, report.grades):
                row = {
                    "arm": arm_name,
                    "task_id": attempt.task_id,
                    "family": getattr(attempt, "family", "unknown"),
                    "rep": 1,
                    "task_pass": grade.passed,
                    "criteria_passed": 1 if grade.passed else 0,
                    "criteria_total": 1,
                    "answer": attempt.answer or "",
                    "answer_empty": not bool(attempt.answer),
                    "model": client.model,
                    "base_url": client.base_url,
                    "reason": grade.reason,
                }
                rows.append(row)
                line = json.dumps(row, sort_keys=True) + "\n"
                _retry_write(
                    lambda: (rows_handle.write(line), rows_handle.flush()),
                    f"row {attempt.task_id}",
                )
                receipt_task_grade(
                    chain, arm_name, attempt.task_id, grade.passed, grade.reason
                )
    verdict = grade_run(rows_path, Path(gold_path), target=target)
    return {
        "status": "ran",
        "provider": provider,
        "model": client.model,
        "base_url": client.base_url,
        "verdict": verdict["verdict"],
        "rows": str(rows_path),
        "receipts": str(out / "receipts.json"),
        "receipts_ok": verify_receipt_chain(out / "receipts.json")["ok"],
    }


def plan_sweep(configs: List[str]) -> Dict[str, Any]:
    """G.3: a sweep PLANS its receipts - one chain per config, one budget, one
    world. Execution is a separate, explicit act."""
    plan = []
    for index, text in enumerate(configs):
        provider, model = parse_config(text)
        plan.append(
            {
                "index": index,
                "provider": provider,
                "model": model or "(provider default)",
                "receipts": f"results/sweep-{index:02d}-{provider}/receipts.json",
                "rows": f"results/sweep-{index:02d}-{provider}/results.jsonl",
            }
        )
    return {"configs": plan, "execute": "pass --execute to run them"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="operator mode")
    parser.add_argument("--provider", default="nvidia")
    parser.add_argument("--model", default="", help="default: the provider's model")
    parser.add_argument("--spec", type=Path, default=BENCH_DIR / "spec" / "arms.json")
    parser.add_argument(
        "--manifest", type=Path, default=BENCH_DIR / "world" / "world_manifest.json"
    )
    parser.add_argument(
        "--gold",
        type=Path,
        default=BENCH_DIR / "world" / "private" / "gold_world2.json",
    )
    parser.add_argument(
        "--sandbox", type=Path, default=BENCH_DIR / "world" / "world_files"
    )
    parser.add_argument("--out", type=Path, default=BENCH_DIR / "results" / "operator")
    parser.add_argument("--target", type=float, default=0.70)
    parser.add_argument("--via-relay", action="store_true")
    parser.add_argument("--sweep", default="", help="comma-separated provider:model")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--freeze-record",
        type=Path,
        default=None,
        help="freeze record to check against (default: the committed one)",
    )
    args = parser.parse_args(argv)

    try:
        if args.sweep:
            configs = [c.strip() for c in args.sweep.split(",") if c.strip()]
            if args.execute:
                outcomes = []
                for index, text in enumerate(configs):
                    provider, model = parse_config(text)
                    outcomes.append(
                        run_config(
                            provider,
                            model,
                            args.spec,
                            args.manifest,
                            args.gold,
                            args.sandbox,
                            BENCH_DIR / "results" / f"sweep-{index:02d}-{provider}",
                            target=args.target,
                            via_relay=args.via_relay,
                            execute=True,
                            freeze_record=args.freeze_record,
                        )
                    )
                print(json.dumps({"sweep": outcomes}, indent=2))
            else:
                print(json.dumps(plan_sweep(configs), indent=2))
            return 0
        outcome = run_config(
            args.provider,
            args.model,
            args.spec,
            args.manifest,
            args.gold,
            args.sandbox,
            args.out,
            target=args.target,
            via_relay=args.via_relay,
            execute=True,
            freeze_record=args.freeze_record,
        )
        print(json.dumps(outcome, indent=2))
        return 0 if outcome["verdict"] == "PASS" else 1
    except (BenchmarkError, FreezeError) as exc:
        print(f"OPERATOR RUN REFUSED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
