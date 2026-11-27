"""D.5 and D.6: self-verification happens in the clean sandbox, and the run log
cannot be edited after the fact without failing an audit.

The self-verify used to write its deliverable to the working tree - the tree
bench's own leakage guard rejects - and nothing receipted the log, so a "human
note" appended afterwards was undetectable.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

import run_world  # noqa: E402
from receipts import verify_hashed_file, verify_receipt_chain  # noqa: E402
from world.paths import GOLD_PATH, MANIFEST_PATH  # noqa: E402


class StubClient:
    @property
    def call_count(self) -> int:
        return 0

    @property
    def total_seconds(self) -> float:
        return 0.0

    def complete(self, messages: List[Dict[str, str]], max_tokens: int = 500) -> str:
        return "ls -la"


def _stub_run(monkeypatch, tmp_path, out):
    """A full dry run with the model, discovery and self-verify stubbed."""
    gold = json.loads(GOLD_PATH.read_text())["answers"]
    seen_paths: List[Path] = []

    def stub_verify(path):
        seen_paths.append(Path(path))
        return {"overall": "pass", "checks": []}

    monkeypatch.setattr(run_world, "run_discovery", lambda wd: tmp_path / "d.md")
    monkeypatch.setattr(run_world, "verify_deliverable", stub_verify)
    monkeypatch.setattr(run_world, "write_verification_report", lambda v: None)
    monkeypatch.setattr(
        run_world,
        "run_hypothesis_task",
        lambda **kw: json.dumps(gold[kw["task_id"]]["gold_answer"], sort_keys=True),
    )
    result = run_world.run_arm(MANIFEST_PATH, "AUDIT", out)
    return result, seen_paths


def test_self_verification_happens_inside_the_sandbox(monkeypatch, tmp_path):
    """D.5: the deliverable is written where the arm worked - a tree that
    provably holds no gold - not in the working tree."""
    before = set(REPO_ROOT.glob("deliverable_*.txt"))
    result, seen_paths = _stub_run(monkeypatch, tmp_path, tmp_path / "run")
    after = set(REPO_ROOT.glob("deliverable_*.txt"))

    assert seen_paths, "self-verify was never called"
    for path in seen_paths:
        # the tempfile sandbox, not the working tree and not pytest's tmp
        assert path.parent.name.startswith("verdict-sandbox-"), path
        assert "private" not in str(path) and "gold" not in str(path), path
    assert before == after, "a deliverable leaked into the working tree"


def test_a_clean_run_receipts_its_log_and_audits_green(monkeypatch, tmp_path):
    out = tmp_path / "run"
    result, _seen = _stub_run(monkeypatch, tmp_path, out)
    assert result["receipts"]["ok"] is True
    assert result["log_audit"]["ok"] is True
    log = out / "hypothesis_log.md"
    assert log.is_file()
    assert verify_hashed_file(out / "receipts.json", log)["ok"] is True


def test_a_synthetic_human_edit_to_the_log_fails_the_audit(monkeypatch, tmp_path):
    """D.6: append a note to the run log after the fact. The receipt chain
    itself is untouched - the ARTIFACT was edited - and the audit says so."""
    out = tmp_path / "run"
    _result, _seen = _stub_run(monkeypatch, tmp_path, out)
    log = out / "hypothesis_log.md"
    with log.open("a") as handle:
        handle.write("\n<!-- human note: this run looked off, adjusted -->\n")

    # the chain still verifies - the receipts were not touched
    assert verify_receipt_chain(out / "receipts.json")["ok"] is True
    # but the artifact no longer matches what was receipted
    audit = verify_hashed_file(out / "receipts.json", log)
    assert audit["ok"] is False
    assert audit["recorded"] != audit["actual"]


def test_the_run_log_is_written_only_by_the_harness(monkeypatch, tmp_path):
    """The log's only writer is HypothesisEngine.write_log. The receipt per task
    is what makes any other writer detectable."""
    out = tmp_path / "run"
    _result, _seen = _stub_run(monkeypatch, tmp_path, out)
    receipts = json.loads((out / "receipts.json").read_text())
    log_rows = [r for r in receipts["rows"] if r.get("kind") == "hypothesis_log"]
    assert len(log_rows) == 25  # one per task, harness-authored
    assert all("sha256" in r for r in log_rows)
