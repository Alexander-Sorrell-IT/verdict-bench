"""Tamper-evident receipts — chain integrity, tamper detection, language stability.

Adapted from Alexander-Sorrell-IT/acme-refund-agent/tests/test_receipts.py with the hash
rule kept identical to the original, so both chains re-verify with the same algorithm.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))

import receipts as receipts  # noqa: E402


@pytest.fixture
def chain(tmp_path):
    """A chain in a temp dir. ReceiptChain takes an explicit path, so no monkeypatching."""
    return receipts.ReceiptChain(tmp_path / "receipts.json")


def _seed(chain, count=4):
    """Four receipts that look like a real graded run, not a placeholder."""
    rows = []
    for i in range(count):
        rows.append(
            chain.append(
                {
                    "kind": "task_grade",
                    "arm": "AF2",
                    "task_id": f"W2-{i:03d}",
                    "passed": "true" if i % 2 == 0 else "false",
                    "reason": "exact hash match" if i % 2 == 0 else "hash mismatch",
                }
            )
        )
    return rows


def test_chain_verifies_and_tamper_is_caught(chain):
    """The load-bearing test: flip one byte, chain goes red, and names the row."""
    _seed(chain)
    assert chain.verify()["ok"] is True

    payload = json.loads(chain.path.read_text())
    payload["rows"][2]["task_id"] = "W2-999"  # one byte changed, hash left alone
    chain.path.write_text(json.dumps(payload))

    result = chain.verify()
    assert result["ok"] is False
    assert result["broken_seq"] == 2


def test_genesis_prev_is_zeros(chain):
    row = chain.append({"kind": "run_manifest", "model": "x"})
    assert row["prev_hash"] == "0" * 64


def test_each_row_binds_its_predecessor(chain):
    """Row N's hash must differ if row N-1 changes. That is what makes a chain."""
    rows = _seed(chain)
    assert rows[1]["prev_hash"] == rows[0]["hash"]
    assert rows[2]["prev_hash"] == rows[1]["hash"]


def test_float_and_int_hash_identically(chain):
    """A float and its text form cannot hash differently — JS/Python drift defence."""
    row_int = chain.append({"kind": "t", "wall_seconds": 2700})
    other = receipts.ReceiptChain(chain.path.parent / "second.json")
    row_float = other.append({"kind": "t", "wall_seconds": 2700.0})
    assert row_int["wall_seconds"] == row_float["wall_seconds"] == "2700"


def test_none_becomes_empty_string_not_null(chain):
    row = chain.append({"kind": "t", "reason": None})
    assert row["reason"] == ""


def test_offline_verification_needs_nothing_but_the_file(tmp_path):
    """An independent scorer can verify from the file alone."""
    chain = receipts.ReceiptChain(tmp_path / "r.json")
    _seed(chain)
    assert receipts.verify_receipt_chain(tmp_path / "r.json")["ok"] is True


def test_render_verification_reports_both_outcomes(tmp_path):
    chain = receipts.ReceiptChain(tmp_path / "r.json")
    _seed(chain)
    ok, line = receipts.render_verification(tmp_path / "r.json")
    assert ok and "chain intact" in line

    payload = json.loads(chain.path.read_text())
    payload["rows"][0]["reason"] = "fabricated"
    chain.path.write_text(json.dumps(payload))
    ok, line = receipts.render_verification(tmp_path / "r.json")
    assert not ok and "BROKEN" in line


def test_run_manifest_and_grade_receipts_compose(chain):
    """The two receipt kinds a run emits, chained in one log."""
    manifest = chain.append(
        {"kind": "run_manifest", "model": "nemotron", "world_hash": "abc123"}
    )
    grade = chain.append(
        {
            "kind": "task_grade",
            "arm": "A",
            "task_id": "C1",
            "passed": "true",
            "reason": "exact hash match",
        }
    )
    assert grade["prev_hash"] == manifest["hash"]
    assert chain.verify()["ok"] is True
