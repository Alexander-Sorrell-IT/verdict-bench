#!/usr/bin/env python3
# verdict-bench world/gates.py
#
# Gates a world must pass BEFORE it may be used. A world that cannot be verified in code
# does not ship - that is the rule alignerr-task-factory enforces ("Refuses to ship
# paradoxes") and it is the missing piece that let a contaminated world be benchmarked.
#
# Three patterns ported from alignerr-task-factory/verify/gates.py:
#
#   1. LEAK GATE - solution_answer_key_leak: nothing gold-derived may appear in the
#      agent-visible tree.
#   2. SEALED GRADER SIGNATURE - the grader takes gold as an ARGUMENT, never reads it
#      from a path inside the agent-visible tree. (graded against grader.py's signature)
#   3. COVERAGE REPORT - print what these gates CANNOT check. Green here means
#      structurally sound, not that the benchmark is fair.
#
# Exit code 1 on any blocking failure. Silence is not a pass.

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# The gold path convention lives in world/paths.py. Insert the repo root so this file
# works both as `python -m world.gates` and as `python3 world/gates.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from world.paths import GOLD_FILENAME, PRIVATE_DIRNAME  # noqa: E402

# Keys that must never reach an agent, whatever file they are in. "family" is the
# singular: per-task family entries leak the five-family taxonomy even after the
# root "families" list is gone.
LEAKY_KEYS = frozenset(
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

# Filenames that must never sit inside the agent-visible tree.
GOLD_SHAPED = ("gold", "freeze", "answer_key", "solution")

# A prompt may not contain a formula that produces gold.
BANNED_PROMPT_TOKENS = (
    "MANDATORY COMMANDS",
    "copy exactly",
    "world_files/*.bin",
    "world_files/*.conf",
    "world_files/*.log",
)


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat() + "Z"


def check_world(
    world_dir: Path, sandbox_dir: Path, gold_path: Optional[Path] = None
) -> Tuple[List[Dict[str, str]], List[str]]:
    """Run every world gate. Returns (results, blocking_failures)."""
    results: List[Dict[str, str]] = []
    # Gold is withheld by default: it lives under the world's private root, never
    # beside the manifest in the agent-visible tree.
    gold_path = gold_path or (world_dir / PRIVATE_DIRNAME / GOLD_FILENAME)

    def record(name: str, ok: bool, detail: str = "") -> bool:
        results.append(
            {"name": name, "status": "PASS" if ok else "FAIL", "detail": detail}
        )
        return ok

    # ---- structural -------------------------------------------------------------
    world_files = world_dir / "world_files"
    record(
        "structure:world_files-exists",
        world_files.is_dir(),
        f"{world_files} missing - the agent has no data to work on",
    )
    if world_files.is_dir():
        populated = sorted(p for p in world_files.iterdir() if p.is_file())
        record(
            "structure:world_files-populated",
            bool(populated),
            f"{len(populated)} files",
        )

    manifest_path = world_dir / "world_manifest.json"
    record("structure:manifest-exists", manifest_path.is_file())
    manifest = _read_json(manifest_path)
    record("structure:manifest-parses", manifest is not None)

    record(
        "structure:gold-exists",
        gold_path.is_file(),
        f"{gold_path} missing - nothing to grade against",
    )
    gold = _read_json(gold_path) or {}
    answers = gold.get("answers", gold)

    # ---- separation: gold must not be reachable from the agent's tree ----------
    # The agent's tree is BOTH the arm's sandbox and the world's public files
    # (world_dir minus the withheld private root). A gold file sitting beside the
    # manifest is invisible to a sandbox-only scan - that is the gap this closes.
    record(
        "separation:gold-outside-sandbox",
        not _inside(gold_path, sandbox_dir),
        f"gold at {gold_path} is inside {sandbox_dir}",
    )

    public_files = _agent_visible_files(world_dir)
    record(
        "separation:gold-outside-public-tree",
        gold_path.resolve() not in {p.resolve() for p in public_files},
        f"gold at {gold_path} sits in the agent-visible tree",
    )

    for marker in GOLD_SHAPED:
        hits = [
            p for p in _files(sandbox_dir) + public_files if marker in p.name.lower()
        ]
        record(
            f"separation:no-gold-filename:{marker}",
            not hits,
            f"{[h.name for h in hits][:3]}",
        )

    # ---- leak gate: the answer KEY must not appear in the agent-visible tree ----
    # What must not leak is the key that maps task -> verdict: the gold file itself and
    # its hashes. What MUST be reachable is the data the answer is derived from - for
    # "find the value in the file" tasks, the answer IS in the public file. A gate that
    # rejected derived values would reject every legitimate world, which would make the
    # gate theatre rather than protection.
    canaries: List[str] = []
    for rec in answers.values():
        if not isinstance(rec, dict):
            continue
        if "gold_hash" in rec:
            canaries.append(str(rec["gold_hash"]))
    canaries = sorted({c for c in canaries if len(c) >= 8})
    canaries.append(json.dumps(gold, sort_keys=True))
    scanned = _files(sandbox_dir) + public_files
    leaking = _scan_files_for(scanned, canaries)
    record(
        "leak:no-answer-key-in-public-tree",
        not leaking,
        f"{[(f.name, c[:12]) for f, c in leaking][:4]}",
    )
    # ---- manifest must not carry the taxonomy or the rules ----------------------
    if manifest:
        leaked = sorted(LEAKY_KEYS & set(manifest))
        record("manifest:no-leaked-keys", not leaked, f"{leaked}")
        for task in manifest.get("tasks", []):
            task_leaks = sorted(LEAKY_KEYS & set(task))
            record(
                f"manifest:task-clean:{task.get('task_id', '?')}",
                not task_leaks,
                f"{task_leaks}",
            )
            prompt = str(task.get("description", ""))
            banned = [b for b in BANNED_PROMPT_TOKENS if b in prompt]
            record(
                f"prompt:no-answer-formula:{task.get('task_id', '?')}",
                not banned,
                f"{banned}",
            )

    # ---- the grader must take gold as an argument, not read it from the tree ---
    grader_path = world_dir / "grader.py"
    if grader_path.is_file():
        source = grader_path.read_text(errors="replace")
        record(
            "grader:takes-gold-as-argument",
            "grade_world(results_path" in source and "gold_path" in source,
            "grade_world must accept a gold path argument",
        )
        record(
            "grader:no-llm-judge",
            "chat/completions" not in source and "ollama" not in source.lower(),
            "the grader must not call a model",
        )

    # ---- the independent solver must exist and not call a model ---------------
    # A MISSING solver is blocking, not skipped. Silence is not a pass: gates that
    # check nothing because their target was deleted are how a paradox ships.
    solver_path = world_dir / "independent_solver.py"
    if solver_path.is_file():
        source = solver_path.read_text(errors="replace")
        record("solver:exists", True)
        record(
            "solver:no-llm",
            "chat/completions" not in source and "ollama" not in source.lower(),
            "the solver must derive gold from public files only, in code",
        )
        record(
            "solver:not-a-stub",
            "solved = False" not in source,
            "a stubbed solver verifies nothing",
        )
    else:
        record(
            "solver:exists",
            False,
            f"{solver_path} missing - nothing proves the tasks are solvable "
            "from public files, so the world may not be used",
        )

    blocking = [r["name"] for r in results if r["status"] == "FAIL"]
    return results, blocking


def report(results: List[Dict[str, str]], blocking: List[str]) -> str:
    """Human-readable gate report, including what these gates CANNOT check."""
    passed = sum(1 for r in results if r["status"] == "PASS")
    width = max(len(r["name"]) for r in results) + 2
    lines: List[str] = ["", "WORLD GATES"]
    for r in results:
        mark = "PASS" if r["status"] == "PASS" else "FAIL"
        detail = f"   {r['detail']}" if r["detail"] and r["status"] == "FAIL" else ""
        lines.append(f"  [{mark}] {r['name']:<{width}}{detail}")

    lines.append("")
    lines.append("  COVERAGE - what these gates CANNOT check:")
    for uncovered in (
        "that the tasks are actually hard (a trivially guessable world passes every gate)",
        "that the answer formulas are absent from the MODEL's context window at runtime",
        "that an arm cannot reach gold through the model, a tool, or the OS",
        "that the gold is CORRECT - a verifiable wrong answer is still wrong",
        "that the run was fair (budget, load, retries) - that is bench.py's job",
    ):
        lines.append(f"    - {uncovered}")

    lines.append("")
    lines.append(f"  {passed}/{len(results)} gates passed")
    if blocking:
        lines.append(f"  BLOCKING: {', '.join(blocking)}")
        lines.append("  This world may not be used.")
    else:
        lines.append("  Structurally sound - NOT a claim that the benchmark is fair.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(
            "usage: python3 world/gates.py <world_dir> <sandbox_dir> [gold_path]",
            file=sys.stderr,
        )
        return 2
    world_dir = Path(argv[0])
    sandbox_dir = Path(argv[1])
    gold_path = Path(argv[2]) if len(argv) > 2 else None

    results, blocking = check_world(world_dir, sandbox_dir, gold_path)
    print(report(results, blocking))
    return 1 if blocking else 0


# ------------------------------------------------------------------ helpers


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _files(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def _agent_visible_files(world_dir: Path) -> List[Path]:
    """Every file an arm could read: the world dir minus the withheld private root
    (and bytecode caches, which are not content). Withholding wins over scope: a
    "private" directory at ANY depth is excluded, not just the root's."""
    public: List[Path] = []
    for path in sorted(world_dir.rglob("*")):
        if not path.is_file():
            continue
        if PRIVATE_DIRNAME in path.parts:
            continue
        if "__pycache__" in path.parts:
            continue
        public.append(path)
    return public


def _scan_files_for(files: List[Path], values: List[str]) -> List[Tuple[Path, str]]:
    """Which of these files contain which gold value."""
    hits: List[Tuple[Path, str]] = []
    for path in files:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for value in values:
            if value in text:
                hits.append((path, value))
                break
    return hits


def _files_containing(root: Path, values: List[str]) -> List[Tuple[Path, str]]:
    """Files in the agent-visible tree containing a gold value."""
    return _scan_files_for(_files(root), values)


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


if __name__ == "__main__":
    raise SystemExit(main())
