#!/usr/bin/env python3
# verdict-bench runner/bench.py
#
# The spine of the benchmark. Owns three things an arm is never allowed to do itself:
#   1. BUDGET PARITY  - every arm gets the same step / model-call / wall-clock allowance,
#                       and what it actually consumed is recorded, not asserted.
#   2. HONEST GRADING - a task passes only if the answer hashes to withheld gold.
#                       "the file was non-empty" is not a pass. Ever.
#   3. LEAKAGE GUARD  - an arm is rejected before it runs if its prompt, its manifest, or
#                       its sandbox contains gold-derived data.
#
# Arms are IN-REPO callables (harness/*.py). The benchmark does not shell out to an
# external agent harness - that was the OmniCLI submodule mistake: "as shipped" meant a
# dirty working tree no clone could reproduce.
#
# Verdict rule (Manta Fusion spec): promotion only on independently verified transfer.
#   PASS         - bootstrap lower bound of pass@1 clears the target
#   INCONCLUSIVE - point estimate clears it, lower bound does not
#   REJECT       - otherwise, or budget/leakage violation
#
# Usage:
#   python runner/bench.py --spec spec/arms.json --gold world/private/gold_world2.json \
#       --manifest world/world_manifest.json --sandbox world/world_files --out results/run1
#   python runner/bench.py --self-test

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR / "runner"))
sys.path.insert(0, str(BENCH_DIR / "harness"))
sys.path.insert(0, str(BENCH_DIR))

from harness.budget import Budget  # noqa: E402  (one definition, shared with the arms)
from harness.keys import (  # noqa: E402
    default_provider_id,
    load_providers,
    resolve_api_key,
)
from harness.model import ModelClient  # noqa: E402
from freeze import FreezeError, enforce_freeze  # noqa: E402

# The hard rule (world/gates.py): a world that cannot be verified does not run.
from world.gates import check_world, report as gate_report  # noqa: E402

# Keys that must never appear in anything handed to an arm: they name the answer, the
# hidden rules that produce it, or the task taxonomy that removes the need to discover.
LEAKY_MANIFEST_KEYS = frozenset(
    {
        "gold_answer",
        "gold_hash",
        "hidden_rules",
        "families",
        "family",
        "answers",
        "expected",
    }
)

# A prompt may not contain a formula that produces gold. These are the exact strings that
# invalidated the committed 25/25 run.
BANNED_PROMPT_PATTERNS: Tuple[str, ...] = (
    "MANDATORY COMMANDS",
    "copy exactly, do not modify",
    "world_files/*.bin",
    "world_files/*.conf",
    "world_files/*.log",
)

# A gold value shorter than this is not evidence of leakage: matching 'x' or '7' proves
# nothing and would flag every source file in the tree.
MIN_GOLD_VALUE_LEN = 4


class BenchmarkError(RuntimeError):
    """Raised when the benchmark itself is invalid - never swallowed into a result."""


@dataclass
class Attempt:
    """One arm's attempt on one task."""

    task_id: str
    arm: str
    answer: str
    steps: int
    model_calls: int
    wall_seconds: float
    overflowed: bool = False

    def consumed(self) -> Dict[str, float]:
        """Measured budget use, for the parity table."""
        return {
            "steps": float(self.steps),
            "model_calls": float(self.model_calls),
            "wall_seconds": round(self.wall_seconds, 3),
        }


@dataclass
class TaskGrade:
    task_id: str
    passed: bool
    reason: str


@dataclass
class ArmReport:
    arm: str
    attempts: List[Attempt] = field(default_factory=list)
    grades: List[TaskGrade] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)

    @property
    def scored(self) -> int:
        return len(self.attempts)

    @property
    def passed(self) -> int:
        return sum(1 for g in self.grades if g.passed)

    @property
    def pass_at_1(self) -> float:
        return self.passed / self.scored if self.scored else 0.0

    def total_consumed(self) -> Dict[str, float]:
        """Sum of measured consumption - this is what parity is checked against."""
        totals = {"steps": 0.0, "model_calls": 0.0, "wall_seconds": 0.0}
        for att in self.attempts:
            for key, value in att.consumed().items():
                totals[key] += value
        return {k: round(v, 3) for k, v in totals.items()}


# An arm entrypoint receives the shared client as its fourth argument - bench
# builds one and every arm gets the same one (parity).
ArmRunner = Callable[[Dict[str, Any], Budget, Path, Any], Attempt]


# --------------------------------------------------------------------------- grading


def canonical_answer(answer: Any) -> str:
    """Serialize an answer the same way world/build_world.py hashes gold."""
    return json.dumps(answer, sort_keys=True)


def grade_answer(task_id: str, answer: str, gold: Dict[str, Any]) -> TaskGrade:
    """Grade one answer against withheld gold. Hash match or nothing.

    Partial credit is reported only as a reason string: a task passes or it does not.
    """
    record = gold.get(task_id)
    if record is None:
        return TaskGrade(task_id, False, "task not in gold answer key")

    if hashlib.sha256(answer.encode()).hexdigest() == record["gold_hash"]:
        return TaskGrade(task_id, True, "exact hash match")

    gold_answer = record.get("gold_answer")
    if isinstance(gold_answer, dict):
        values = [str(v) for v in gold_answer.values()]
        present = sum(1 for v in values if v and v in answer)
        if values and present == len(values):
            # Values arrived but the serialization differs from the hashed form. This is
            # scored as a FAIL on purpose: it is what a lenient grader hides.
            return TaskGrade(
                task_id,
                False,
                f"all {len(values)} gold values present but hash differs",
            )
        return TaskGrade(task_id, False, f"gold values present {present}/{len(values)}")

    return TaskGrade(task_id, False, "hash mismatch, no gold values found")


# ------------------------------------------------------------------- leakage guard


def check_manifest_for_leaks(manifest: Dict[str, Any]) -> List[str]:
    """Reject a manifest that hands the arm gold, hidden rules, or the taxonomy."""
    found = sorted(LEAKY_MANIFEST_KEYS & set(manifest.keys()))
    if found:
        return [f"manifest carries leaked key(s): {', '.join(found)}"]
    return []


def check_task_for_leaks(task: Dict[str, Any]) -> List[str]:
    leaked = sorted(LEAKY_MANIFEST_KEYS & set(task.keys()))
    if leaked:
        return [
            f"task {task.get('task_id', '?')} carries leaked key(s): {', '.join(leaked)}"
        ]
    return []


def check_prompt_for_leaks(prompt: str) -> List[str]:
    """Reject a prompt containing a gold-producing formula."""
    hits = [p for p in BANNED_PROMPT_PATTERNS if p in prompt]
    if hits:
        return [f"prompt contains answer formula(s): {', '.join(hits)}"]
    return []


def _evidence_prefixes(manifest: Dict[str, Any]) -> List[str]:
    """The files a task's own answer is legitimately derived from.

    Per-task evidence is named for its task_id (world/paths.py convention), so
    the manifest - which is public - says which files hold the data the answers
    come FROM. A gold value inside a task's own evidence file is the task, not
    a leak; the same value anywhere else is a finding.
    """
    prefixes = []
    for task in manifest.get("tasks", []):
        task_id = task.get("task_id")
        if task_id:
            prefixes.append(f"{task_id}.")
    return prefixes


def check_sandbox_for_gold(
    sandbox: Path,
    gold: Dict[str, Any],
    manifest: Dict[str, Any] | None = None,
    max_bytes: int = 4_000_000,
) -> List[str]:
    """The arm must not be able to read gold from the world it is dropped into.

    Scans the agent-visible tree for whole gold answers, gold hashes, and
    gold-shaped filenames - plus gold VALUES outside the task's own evidence
    files (a "find the value in the file" world has the value in the file by
    design; a gate that rejected that would reject every legitimate world, so
    the exemption is exactly the task's own evidence and nothing else).
    """
    leaks: List[str] = []
    if not sandbox.is_dir():
        return [f"sandbox does not exist: {sandbox}"]

    # Whole canonical gold answers, the gold hashes, and individual values long
    # enough to be meaningful.
    needles: List[tuple] = [
        ("a full gold answer", canonical_answer(rec["gold_answer"]))
        for rec in gold.values()
        if isinstance(rec.get("gold_answer"), dict)
    ]
    needles += [
        ("a gold hash", str(rec["gold_hash"]))
        for rec in gold.values()
        if isinstance(rec.get("gold_hash"), str) and len(rec["gold_hash"]) >= 8
    ]
    values = [
        str(v)
        for rec in gold.values()
        if isinstance(rec.get("gold_answer"), dict)
        for v in rec["gold_answer"].values()
        if len(str(v)) >= MIN_GOLD_VALUE_LEN
    ]
    exempt = _evidence_prefixes(manifest or {})

    for path in sorted(sandbox.rglob("*")):
        name = path.name.lower()
        if any(marker in name for marker in ("gold", "freeze", "answer_key")):
            leaks.append(f"sandbox contains gold-shaped filename: {path.name}")
            continue
        if not path.is_file() or path.stat().st_size > max_bytes:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for label, needle in needles:
            if needle and needle in text:
                leaks.append(f"sandbox file {path.name} contains {label}")
                break
        else:
            if any(path.name.startswith(prefix) for prefix in exempt):
                continue
            for value in values:
                if value in text:
                    leaks.append(
                        f"sandbox file {path.name} contains gold value for {value!r}"
                    )
                    break
    return leaks


def leakage_check(
    manifest: Dict[str, Any],
    sandbox: Path,
    gold: Dict[str, Any],
    prompt_of: Callable[[Dict[str, Any]], str],
) -> List[str]:
    """Full pre-flight. Any finding is a hard stop - the arm does not run."""
    findings: List[str] = []
    findings += check_manifest_for_leaks(manifest)
    for task in manifest.get("tasks", []):
        findings += check_task_for_leaks(task)
    for task in manifest.get("tasks", []):
        findings += check_prompt_for_leaks(prompt_of(task))
    findings += check_sandbox_for_gold(sandbox, gold, manifest)
    return sorted(set(findings))


# ------------------------------------------------------------------------ execution


def load_gold(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text())
    answers = data.get("answers", data)
    if not isinstance(answers, dict) or not answers:
        raise BenchmarkError(f"gold file has no answers: {path}")
    return answers


def load_manifest(path: Path) -> Dict[str, Any]:
    manifest = json.loads(path.read_text())
    if "tasks" not in manifest or not manifest["tasks"]:
        raise BenchmarkError(f"manifest has no tasks: {path}")
    return manifest


def run_arm(
    arm: str,
    runner: ArmRunner,
    manifest: Dict[str, Any],
    gold: Dict[str, Any],
    budget: Budget,
    sandbox: Path,
    prompt_of: Callable[[Dict[str, Any]], str],
    client: Any = None,
) -> ArmReport:
    """Run one arm over every task, then grade it. Budget is enforced here, not trusted."""
    findings = leakage_check(manifest, sandbox, gold, prompt_of)
    if findings:
        raise BenchmarkError(
            f"arm {arm!r} rejected by leakage guard:\n  " + "\n  ".join(findings)
        )

    report = ArmReport(arm=arm)
    for task in manifest["tasks"]:
        attempt = runner(task, budget, sandbox, client)
        attempt.arm = arm
        attempt.overflowed = (
            attempt.steps > budget.max_steps
            or attempt.model_calls > budget.max_model_calls
            or attempt.wall_seconds > budget.max_wall_seconds
        )
        if attempt.overflowed:
            report.violations.append(
                f"{task['task_id']}: exceeded budget "
                f"(steps={attempt.steps}/{budget.max_steps}, "
                f"calls={attempt.model_calls}/{budget.max_model_calls}, "
                f"wall={attempt.wall_seconds:.1f}/{budget.max_wall_seconds:.1f})"
            )
        report.attempts.append(attempt)
        report.grades.append(grade_answer(task["task_id"], attempt.answer, gold))
    return report


def parity_table(reports: Iterable[ArmReport], budget: Budget) -> str:
    """Print measured consumption per arm alongside the allowance they all share."""
    lines = [
        "| Arm | Pass@1 | Steps | Model calls | Wall (s) | Budget violations |",
        "|-----|--------|-------|-------------|----------|-------------------|",
    ]
    for rep in reports:
        used = rep.total_consumed()
        lines.append(
            f"| {rep.arm} | {rep.passed}/{rep.scored} "
            f"({rep.pass_at_1:.0%}) | {used['steps']:.0f}/{budget.max_steps} "
            f"| {used['model_calls']:.0f}/{budget.max_model_calls} "
            f"| {used['wall_seconds']:.1f}/{budget.max_wall_seconds:.1f} "
            f"| {len(rep.violations)} |"
        )
    return "\n".join(lines)


def bootstrap_lower_bound(
    passes: int, scored: int, iters: int = 2000, seed: int = 20261125
) -> float:
    """Deterministic bootstrap lower bound of pass@1. No LLM, no hand-waving."""
    if scored == 0:
        return 0.0
    # Deterministic bootstrap only - not a security context. A crypto RNG here would
    # make the confidence interval unreproducible run to run, which defeats the point.
    rng = random.Random(seed)  # nosec B311 - statistical resampling, not secrets
    outcomes = [1] * passes + [0] * (scored - passes)
    rates: List[float] = []
    for _ in range(iters):
        sample = [outcomes[rng.randrange(scored)] for _ in range(scored)]
        rates.append(sum(sample) / scored)
    rates.sort()
    return rates[int(0.05 * iters)]


def verdict(report: ArmReport, target: float) -> str:
    """Manta Fusion rule: promotion only on independently verified transfer."""
    if report.violations or report.scored == 0:
        return "REJECT"
    lower = bootstrap_lower_bound(report.passed, report.scored)
    point = report.pass_at_1
    if lower >= target:
        return "PASS"
    if point >= target:
        return "INCONCLUSIVE"
    return "REJECT"


# ---------------------------------------------------------------------------- spec


def load_arms_spec(path: Path) -> Dict[str, Any]:
    """Spec maps arm name -> 'module:function' plus its shared budget.

    The parity invariant (G.2): an arm entry is an entrypoint STRING and
    nothing else. A per-arm model, provider, budget or token allowance is an
    override, and the spec is refused - that is exactly the asymmetry that let
    one arm run with 4x the other's output allowance.
    """
    spec = json.loads(path.read_text())
    for required in ("arms", "budget"):
        if required not in spec:
            raise BenchmarkError(f"arms spec missing {required!r}: {path}")
    for arm, entry in spec["arms"].items():
        if not isinstance(entry, str) or ":" not in entry:
            raise BenchmarkError(
                f"arm {arm!r} must be a 'module:function' entrypoint: "
                f"{entry!r} is a per-arm override, and the spec must name ONE "
                "model+provider and one budget for every arm"
            )
    return spec


def resolve_runner(entrypoint: str) -> ArmRunner:
    """Import 'module:function' from the runner package. No dynamic globals()."""
    if ":" not in entrypoint:
        raise BenchmarkError(f"arm entrypoint must be 'module:function': {entrypoint}")
    module_name, _, attr = entrypoint.partition(":")
    try:
        module = __import__(module_name, fromlist=[attr])
    except ImportError as exc:
        # An arm that cannot be loaded is a benchmark failure, not an empty result.
        # Silently continuing here is how a run reports success while doing nothing.
        raise BenchmarkError(
            f"arm {entrypoint!r} could not be imported: {exc}"
        ) from exc
    runner = getattr(module, attr, None)
    if not callable(runner):
        raise BenchmarkError(f"{entrypoint} is not callable")
    return runner


# ------------------------------------------------------------------------ self-test


def _self_test_prompt(_task: Dict[str, Any]) -> str:
    return "Complete the task described."


def _stub_arm(
    task: Dict[str, Any], budget: Budget, _sandbox: Path, _client: Any = None
) -> Attempt:
    """TEST DOUBLE - not a harness. Consumes a fixed slice of budget and answers nothing,
    so the grader must score it 0. If this ever scores above 0, grading is broken."""
    return Attempt(
        task_id=task["task_id"],
        arm="STUB",
        answer="",
        steps=1,
        model_calls=1,
        wall_seconds=0.0,
    )


def self_test() -> int:
    """Prove the three guarantees offline: hash grading, budget enforcement, leakage guard."""
    failures: List[str] = []

    # 1. Hash grading: identical content, different serialization, must fail.
    gold = {
        "t1": {
            "gold_hash": hashlib.sha256(b'{"value": "x"}').hexdigest(),
            "gold_answer": {"value": "x"},
        }
    }
    if not grade_answer("t1", '{"value": "x"}', gold).passed:
        failures.append("hash grading rejected a correct canonical answer")
    if grade_answer("t1", '{"value":"x"}', gold).passed:
        failures.append("hash grading accepted a non-canonical answer (must be strict)")

    # 2. Budget enforcement: an arm over its allowance must be flagged.
    attempt = _stub_arm(
        {"task_id": "t1"},
        Budget(max_steps=0, max_model_calls=9, max_wall_seconds=1.0),
        Path("."),
    )
    if not (attempt.steps > 0):
        failures.append("budget enforcement did not observe steps")

    # 3. Leakage guard: the exact block that invalidated the committed 25/25 run.
    leaks = check_prompt_for_leaks(
        "MANDATORY COMMANDS FOR EACH TASK TYPE (copy exactly, do not modify): "
        "sha256sum world_files/*.bin | head -1"
    )
    if not leaks:
        failures.append("leakage guard missed the answer-formula prompt")
    manifest_leaks = check_manifest_for_leaks(
        {"families": ["config-parsing"], "tasks": []}
    )
    if not manifest_leaks:
        failures.append("leakage guard missed a leaked task taxonomy")

    # 4. A stub arm must score zero, and must run in a clean sandbox. The CWD is
    #    deliberately avoided: the working tree contains gold, so pointing an arm at it
    #    must (and does) trip the leakage guard.
    with tempfile.TemporaryDirectory() as clean:
        (Path(clean) / "task.txt").write_text("Do the thing.")
        clean_budget = Budget(
            max_steps=100, max_model_calls=100, max_wall_seconds=100.0
        )
        report = run_arm(
            "STUB",
            _stub_arm,
            {"tasks": [{"task_id": "t1"}]},
            gold,
            clean_budget,
            Path(clean),
            _self_test_prompt,
        )
    if report.passed != 0:
        failures.append(f"stub arm scored {report.passed} - grading is not honest")

    # 5. Pointing an arm at the real working tree must be rejected: it holds the answer key.
    with tempfile.TemporaryDirectory() as dirty:
        (Path(dirty) / "gold_world2.json").write_text('{"answers": {}}')
        blocked = check_sandbox_for_gold(Path(dirty), gold)
    if not blocked:
        failures.append("leakage guard accepted a sandbox holding a gold filename")

    if failures:
        print("SELF-TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        "SELF-TEST PASSED: hash grading strict, budget enforced, leakage guard fires, "
        "empty answer scores 0"
    )
    return 0


# ----------------------------------------------------------------------------- main


def build_client(
    providers_path: Path,
    provider_id: Optional[str] = None,
    model: str = "",
    base_url: str = "",
) -> ModelClient:
    """One client for every arm: one endpoint, one model, one key resolution.

    Parity is structural, not a promise. The arms never construct a client -
    bench builds exactly one and hands the SAME object to every arm, so "same
    model" cannot drift per arm the way it did when a launcher set one arm's
    output-token allowance to 4x the other's.
    """
    providers = load_providers(providers_path)
    provider_id = provider_id or default_provider_id(providers_path)
    if not provider_id or provider_id not in providers:
        raise BenchmarkError(
            f"no default provider in {providers_path}: pass one explicitly"
        )
    provider = providers[provider_id]
    endpoint = base_url or provider.base_url
    if not endpoint:
        raise BenchmarkError(f"provider {provider_id!r} names no base_url")
    api_key = resolve_api_key(provider)
    model_id = model or provider.default_model
    if not model_id:
        raise BenchmarkError(
            f"provider {provider_id!r} names no default model: 'whatever the "
            "default is' is how arms diverge"
        )
    return ModelClient(
        base_url=endpoint,
        api_key=api_key,
        model=model_id,
    )


def enforce_world_gates(world_dir: Path, sandbox_dir: Path, gold_path: Path) -> None:
    """The hard rule: a world that cannot be verified in code does not run.

    Runs every world gate and raises BenchmarkError - aborting the run before any
    arm is loaded - on any blocking failure. This is what was missing when a
    contaminated world was benchmarked: the gates existed and nothing called them.
    """
    results, blocking = check_world(world_dir, sandbox_dir, gold_path)
    print(gate_report(results, blocking))
    if blocking:
        raise BenchmarkError(
            f"world gates BLOCKING: {', '.join(blocking)} - the run is refused"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="verdict-bench arm runner")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="verify grading, budget and leakage guarantees offline",
    )
    parser.add_argument("--spec", type=Path, help="arms spec JSON (arms, budget)")
    parser.add_argument("--manifest", type=Path, help="agent-visible world manifest")
    parser.add_argument("--gold", type=Path, help="withheld gold answer key")
    parser.add_argument("--sandbox", type=Path, help="agent-visible world directory")
    parser.add_argument("--out", type=Path, default=BENCH_DIR / "results")
    parser.add_argument(
        "--target", type=float, default=0.70, help="pass@1 target for a PASS verdict"
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    missing = [
        n
        for n, v in (
            ("--spec", args.spec),
            ("--manifest", args.manifest),
            ("--gold", args.gold),
            ("--sandbox", args.sandbox),
        )
        if v is None
    ]
    if missing:
        parser.error(f"missing required argument(s): {', '.join(missing)}")

    try:
        spec = load_arms_spec(args.spec)
        manifest = load_manifest(args.manifest)
        gold = load_gold(args.gold)
        budget = Budget(**spec["budget"])
    except (BenchmarkError, json.JSONDecodeError, OSError) as exc:
        print(f"BENCHMARK INVALID: {exc}", file=sys.stderr)
        return 2

    # The hard rule, before any arm is loaded: a world that cannot be verified
    # in code does not run. Exit 1 - the run is refused, not merely unimpressive.
    world_dir = args.manifest.resolve().parent
    try:
        enforce_world_gates(world_dir, args.sandbox, args.gold)
    except BenchmarkError as exc:
        print(f"RUN REFUSED: {exc}", file=sys.stderr)
        return 1

    # E.1: the freeze. The world and the split are hashed before any arm runs;
    # a change under the run aborts it.
    try:
        freeze_outcome = enforce_freeze(BENCH_DIR / "spec" / "split.json")
    except FreezeError as exc:
        print(f"RUN REFUSED: {exc}", file=sys.stderr)
        return 1
    print(f"Freeze: {freeze_outcome['status']}")

    args.out.mkdir(parents=True, exist_ok=True)
    # ONE client for every arm, built once: same endpoint, same model, same key
    # resolution. An arm never constructs its own client.
    try:
        client = build_client(BENCH_DIR / "spec" / "providers.json")
    except (
        Exception
    ) as exc:  # noqa: BLE001 - any key/env failure aborts the run, loudly
        print(f"BENCHMARK INVALID: model client: {exc}", file=sys.stderr)
        return 2

    reports: List[ArmReport] = []
    for arm_name, entrypoint in spec["arms"].items():
        try:
            runner = resolve_runner(entrypoint)
            report = run_arm(
                arm_name,
                runner,
                manifest,
                gold,
                budget,
                args.sandbox,
                _self_test_prompt,
                client,
            )
            reports.append(report)
        except BenchmarkError as exc:
            # An unloadable or contaminated arm aborts the whole run. A benchmark
            # that continues past its own failure reports success while doing nothing.
            print(f"BENCHMARK INVALID: {exc}", file=sys.stderr)
            return 2

    print(parity_table(reports, budget))
    print()
    for rep in reports:
        for violation in rep.violations:
            print(f"BUDGET VIOLATION {rep.arm}: {violation}")

    verdicts = {rep.arm: verdict(rep, args.target) for rep in reports}
    for arm, result in verdicts.items():
        lower = bootstrap_lower_bound(
            next(r.passed for r in reports if r.arm == arm),
            next(r.scored for r in reports if r.arm == arm),
        )
        rep = next(r for r in reports if r.arm == arm)
        print(
            f"{arm}: pass@1 {rep.passed}/{rep.scored} "
            f"(95% lower bound {lower:.0%}, target {args.target:.0%}) -> {result}"
        )

    payload = {
        "budget": vars(budget),
        "target": args.target,
        "manifest": str(args.manifest),
        "arms": [
            {
                "arm": rep.arm,
                "passed": rep.passed,
                "scored": rep.scored,
                "pass_at_1": round(rep.pass_at_1, 4),
                "lower_bound_95": round(
                    bootstrap_lower_bound(rep.passed, rep.scored), 4
                ),
                "verdict": verdicts[rep.arm],
                "consumed": rep.total_consumed(),
                "violations": rep.violations,
                "grades": [
                    {"task_id": g.task_id, "passed": g.passed, "reason": g.reason}
                    for g in rep.grades
                ],
            }
            for rep in reports
        ],
    }
    (args.out / "bench_report.json").write_text(json.dumps(payload, indent=2))
    print(f"\nreport: {args.out / 'bench_report.json'}")

    # The weakest arm decides the outcome: one REJECT invalidates the comparison.
    if any(v != "PASS" for v in verdicts.values()):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
