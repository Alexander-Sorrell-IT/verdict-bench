#!/usr/bin/env python3
# verdict-bench world/independent_solver.py
#
# The independent solver: proves every gold answer is derivable from PUBLIC files
# only, in code, with no LLM. This is the anti-paradox rule made executable - a task
# whose answer cannot be reproduced here is a task that does not ship.
#
# What it reads: world/world_manifest.json (public) and world/world_files/ (public).
# What it never does: consult the gold to solve. gold_path is where the CALLER keeps
# the answer key; this file compares against it the way the grader does. A solver
# that picks its answer out of gold is a grader that lies.
#
# Solving convention (public, derivable): each task's evidence file is named for its
# task_id in world_files/. The manifest is public, so an arm correlates its task with
# its file by listing the directory - no hidden rule, no positional luck.
#
# Family is inferred from the evidence suffix, never from a taxonomy field: the
# manifest carries none, and that is the point.

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from world.paths import FILES_DIR, GOLD_PATH, MANIFEST_PATH  # noqa: E402


def canonical_answer(answer: Dict[str, Any]) -> str:
    """The one serialization gold hashes are computed over (build_world.py's rule)."""
    return json.dumps(answer, sort_keys=True)


def answer_hash(answer: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_answer(answer).encode()).hexdigest()


def _solve_config(evidence: Path) -> Dict[str, Any]:
    """Read the key/value pair out of the config file."""
    line = evidence.read_text().strip().splitlines()[0]
    _key, _sep, value = line.partition("=")
    return {"value": value.strip()}


def _solve_log(evidence: Path) -> Dict[str, Any]:
    """Count the lines carrying the dominant repeated prefix.

    The prefix that repeats is the signal; the one-off INFO/WARNING lines are
    decoys. Counting the largest group is what an arm infers from the file
    itself - it needs no hidden rule to know which pattern was asked for.
    """
    counts: Dict[str, int] = {}
    for raw in evidence.read_text().splitlines():
        prefix, sep, _rest = raw.partition(":")
        if sep:
            counts[prefix] = counts.get(prefix, 0) + 1
    dominant = max(counts.items(), key=lambda kv: kv[1])[0]
    return {"count": counts[dominant]}


def _solve_hash(evidence: Path) -> Dict[str, Any]:
    """SHA-256 of the evidence file, hex."""
    return {"hash": hashlib.sha256(evidence.read_bytes()).hexdigest()}


def _solve_transform(evidence: Path) -> Dict[str, Any]:
    """The script's echoed payload, uppercased - what running it and transforming
    its output produces. The script's text is public evidence; parsing it is the
    code-only equivalent of executing it."""
    for raw in evidence.read_text().splitlines():
        parts = raw.split()
        if parts and parts[0] == "echo":
            return {"transformed": " ".join(parts[1:]).upper()}
    raise ValueError(f"no echo payload found in {evidence.name}")


def _solve_env(evidence: Path) -> Dict[str, Any]:
    """The variable's value, reversed."""
    line = evidence.read_text().strip().splitlines()[0]
    _name, _sep, value = line.partition("=")
    return {"result": value.strip()[::-1]}


_SOLVERS: Dict[str, Callable[[Path], Dict[str, Any]]] = {
    ".conf": _solve_config,
    ".log": _solve_log,
    ".bin": _solve_hash,
    ".sh": _solve_transform,
    ".env": _solve_env,
}


def solve_task(task: Dict[str, Any], files_dir: Path) -> Dict[str, Any]:
    """Solve one task from public files only. Raises if its evidence is absent."""
    task_id = task["task_id"]
    matches = sorted(files_dir.glob(f"{task_id}.*"))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"{task_id}: expected exactly one evidence file named "
            f"{task_id}.*, found {[p.name for p in matches]}"
        )
    solver = _SOLVERS.get(matches[0].suffix)
    if solver is None:
        raise ValueError(f"{task_id}: no solver knows evidence {matches[0].name}")
    return solver(matches[0])


def solve_world(
    manifest_path: Path = MANIFEST_PATH,
    gold_path: Path = GOLD_PATH,
    files_dir: Path = FILES_DIR,
) -> Dict[str, Any]:
    """Solve every task in the manifest from public files and compare to gold.

    Gold is read to COMPARE, never to solve. Returns the per-task record and the
    reproduction rate: 100% is the only value a shippable world produces.
    """
    manifest = json.loads(Path(manifest_path).read_text())
    gold = json.loads(Path(gold_path).read_text())
    answers = gold.get("answers", gold)

    details: List[Dict[str, Any]] = []
    reproduced = 0
    for task in manifest["tasks"]:
        task_id = task["task_id"]
        expected = answers.get(task_id, {}).get("gold_hash", "")
        answer: Dict[str, Any] = {}
        actual = ""
        try:
            answer = solve_task(task, files_dir)
            actual = answer_hash(answer)
            matched = actual == expected
            reason = "hash match" if matched else "hash mismatch"
        except (FileNotFoundError, ValueError) as exc:
            matched = False
            reason = str(exc)
        details.append(
            {
                "task_id": task_id,
                "reproduced": matched,
                "reason": reason,
                "answer": answer,
                "actual_hash": actual,
                "expected_hash": expected,
            }
        )
        reproduced += int(matched)

    return {
        "total": len(details),
        "reproduced": reproduced,
        "rate": reproduced / len(details) if details else 0.0,
        "details": details,
    }


def report(result: Dict[str, Any]) -> str:
    lines = [
        "",
        "INDEPENDENT SOLVER - gold reproduced from public files, in code",
    ]
    for d in result["details"]:
        mark = "PASS" if d["reproduced"] else "FAIL"
        lines.append(f"  [{mark}] {d['task_id']}  {d['reason']}")
    lines.append("")
    lines.append(
        f"  {result['reproduced']}/{result['total']} gold hashes reproduced "
        f"({result['rate']:.0%})"
    )
    if result["reproduced"] != result["total"]:
        lines.append("  This world ships at least one task that cannot be verified")
        lines.append("  in code from public files. It may not be used.")
    else:
        lines.append("  Every task is solvable from public evidence alone.")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else [])
    manifest = Path(argv[0]) if len(argv) > 0 else MANIFEST_PATH
    gold = Path(argv[1]) if len(argv) > 1 else GOLD_PATH
    result = solve_world(manifest_path=manifest, gold_path=gold)
    print(report(result))
    return 0 if result["reproduced"] == result["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
