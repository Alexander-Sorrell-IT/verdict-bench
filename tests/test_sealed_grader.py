"""The sealed grader: isolated from the arms, decisive before results, honest
about a run that lied.

C.1 - it imports no harness/ code, proven by importing it with a BROKEN harness
package first on the path. C.2 - every verdict path is exercised on scripted
rows. C.3 - the verdict and every task grade ride the receipt chain, and a
flipped byte breaks it with the seq.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))

# Package-qualified: a bare self-import of score/ modules resolves twice under
# mypy (the duplicate-module error setup.cfg warns about).
from score.independent_grader import EMPTY_RATE_CEILING, grade_run  # noqa: E402
from world.paths import GOLD_PATH  # noqa: E402


def _gold() -> dict:
    return json.loads(GOLD_PATH.read_text())["answers"]


def _rows(spec: dict) -> Path:
    """Build a rows file: {task_id: (answer_text, task_pass, answer_empty)}."""
    path = Path(tempfile.mkdtemp()) / "results.jsonl"
    lines = []
    for index, (task_id, (answer, recorded, empty)) in enumerate(spec.items()):
        lines.append(
            json.dumps(
                {
                    "arm": "STUB",
                    "task_id": task_id,
                    "family": "unknown",
                    "rep": 1,
                    "task_pass": recorded,
                    "criteria_passed": 1 if recorded else 0,
                    "criteria_total": 1,
                    "valid_json": True,
                    "wall_s": 1.0,
                    "answer": answer,
                    "answer_empty": empty,
                    "split": "dev" if index % 2 else "test",
                    "reason": "scripted",
                }
            )
        )
    path.write_text("\n".join(lines) + "\n")
    return path


def _correct_spec(passes: int, total: int = 25) -> dict:
    """Honest rows: the first `passes` tasks carry the real gold answer and are
    recorded as passing; the rest carry a wrong answer and are recorded as
    failing. Recorded verdicts must match the answers, or the reconciliation
    (correctly) rejects the run."""
    gold = _gold()
    ids = sorted(gold)
    spec = {}
    for index, task_id in enumerate(ids):
        if index < passes:
            answer = json.dumps(gold[task_id]["gold_answer"], sort_keys=True)
            spec[task_id] = (answer, True, False)
        else:
            spec[task_id] = (json.dumps({"value": "wrong"}), False, False)
    return spec


# ------------------------------------------------------------------ C.1 isolation


def test_the_grader_imports_no_harness_code():
    """In a FRESH interpreter: importing the grader must pull in no harness/ code.
    In-process this is worthless - the suite's own imports would fail it."""
    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'score')!r})\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "import independent_grader\n"
        "harness_modules = [m for m in sys.modules if m.split('.')[0] == 'harness']\n"
        "print(harness_modules)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]", f"the sealed grader imported: {proc.stdout}"


def test_the_grader_imports_with_a_broken_harness_package(tmp_path):
    """The strong form: a harness/ that raises on import. If the grader needs an
    arm to grade, this subprocess exits nonzero."""
    broken = tmp_path / "broken"
    (broken / "harness").mkdir(parents=True)
    (broken / "harness" / "__init__.py").write_text(
        'raise ImportError("harness is sealed away from the grader")\n'
    )
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(broken)!r})\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "sys.path.insert(0, {!r})\n".format(str(REPO_ROOT / "score"))
        + "import independent_grader\nprint('isolated')\n"
    )
    proc = subprocess.run(
        [sys.executable, str(probe)], capture_output=True, text=True, cwd=str(tmp_path)
    )
    assert proc.returncode == 0, proc.stderr
    assert "isolated" in proc.stdout


# ------------------------------------------------------------------ C.2 the rules


def test_a_strong_run_verdicts_pass(tmp_path):
    rows = _rows(_correct_spec(24))
    result = grade_run(rows, GOLD_PATH, target=0.70)
    assert result["verdict"] == "PASS"
    assert result["arms"][0]["pass_at_1"] == 24 / 25


def test_an_estimate_that_clears_but_whose_bound_does_not_is_inconclusive(tmp_path):
    rows = _rows(_correct_spec(24))
    result = grade_run(rows, GOLD_PATH, target=0.95)
    assert result["arms"][0]["pass_at_1"] >= 0.95
    assert result["arms"][0]["pass_at_1_lower_95"] < 0.95
    assert result["verdict"] == "INCONCLUSIVE"


def test_a_weak_run_verdicts_reject(tmp_path):
    rows = _rows(_correct_spec(5))
    result = grade_run(rows, GOLD_PATH, target=0.70)
    assert result["verdict"] == "REJECT"


def test_a_mostly_empty_run_is_inconclusive_even_when_the_bound_clears(tmp_path):
    spec = _correct_spec(24)
    ids = sorted(spec)
    for task_id in ids[: int(len(ids) * (EMPTY_RATE_CEILING + 0.04))]:
        answer, recorded, _empty = spec[task_id]
        spec[task_id] = (answer, recorded, True)
    rows = _rows(spec)
    result = grade_run(rows, GOLD_PATH, target=0.70)
    assert result["verdict"] == "INCONCLUSIVE"
    assert result["arms"][0]["empty_answer_rate"] > EMPTY_RATE_CEILING


# ------------------------------------------------------- independence: reconcile


def test_a_flipped_verdict_is_a_fatal_discrepancy(tmp_path):
    """Recorded pass, wrong answer: the run's grading lied. Not a footnote."""
    spec = _correct_spec(24)
    first = sorted(spec)[0]
    answer, _recorded, _empty = spec[first]
    spec[first] = (answer, True, False)  # keep the lie: wrong answer, pass recorded
    wrong = json.dumps({"value": "not the gold value"})
    spec[first] = (wrong, True, False)
    rows = _rows(spec)
    result = grade_run(rows, GOLD_PATH, target=0.70)
    assert len(result["discrepancies"]) == 1
    assert first in result["discrepancies"][0]
    assert result["verdict"] == "REJECT"


def test_honest_rows_reconcile_clean(tmp_path):
    rows = _rows(_correct_spec(25))
    result = grade_run(rows, GOLD_PATH, target=0.70)
    assert result["discrepancies"] == []


# ------------------------------------------------------------------ C.3 receipts


def test_the_verdict_and_every_grade_ride_the_chain(tmp_path):
    rows = _rows(_correct_spec(25))
    receipts = tmp_path / "receipts.json"
    result = grade_run(rows, GOLD_PATH, target=0.70, receipts_path=receipts)
    assert result["receipts"]["ok"] is True
    chain = json.loads(receipts.read_text())
    kinds = [r["kind"] for r in chain["rows"]]
    assert kinds.count("task_grade") == 25
    assert "run_manifest" in kinds


def test_a_flipped_byte_breaks_the_chain_with_the_seq(tmp_path):
    rows = _rows(_correct_spec(25))
    receipts = tmp_path / "receipts.json"
    grade_run(rows, GOLD_PATH, target=0.70, receipts_path=receipts)
    chain = json.loads(receipts.read_text())
    chain["rows"][3]["reason"] = "tampered"
    receipts.write_text(json.dumps(chain, indent=2))

    from receipts import verify_receipt_chain

    verified = verify_receipt_chain(receipts)
    assert verified["ok"] is False
    assert verified["broken_seq"] == 3
