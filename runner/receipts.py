#!/usr/bin/env python3
# verdict-bench runner/receipts.py
#
# Tamper-evident receipts for benchmark results.
#
# Absorbed from Alexander-Sorrell-IT/acme-refund-agent (backend/receipts.py), which is itself
# the on-the-record / glass-box-alpha fold. The hash rule is kept IDENTICAL on purpose:
#
#     SHA256( salt || prev_hash_bytes || canonical_json(row) ), genesis prev = 64 zeros,
#     sorted-keys canonical JSON
#
# so a chain re-verifies OFFLINE, in pure Python or pure Node, with no SDK, no network and
# no trust in us. Flip one byte of any receipt and the chain goes red.
#
# Why this lives here: a run result is currently only as trustworthy as the file it sits
# in. A fabricated 25/25 was committed to this project as fact and nothing in the repo
# could prove otherwise. Chaining the run manifest and every task grade means a result
# carries its own proof, and an independent scorer can confirm it without re-running an arm.
#
# All field VALUES are strings so canonical JSON is byte-identical across languages and
# float-formatting drift (50.0 vs 50) cannot silently invalidate a chain.

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

GENESIS_PREV = "0" * 64
SALT = "verdict-bench:v1"  # public domain-separation string, shipped in the export


def stable_str(value: Any) -> str:
    """Stringify so an int and its equal float agree.

    Prevents the documented cross-language drift: Python str(2700.0) == "2700.0" while the
    same number lands in a JS export as "2700", and the chain stops re-verifying. Booleans
    are lowercased for the same reason.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


class ReceiptError(RuntimeError):
    """Raised when a chain cannot be read or written."""


class ReceiptChain:
    """One hash-chained, tamper-evident log of benchmark receipts."""

    def __init__(self, path: Path, salt: str = SALT) -> None:
        self.path = Path(path)
        self.salt = salt
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ hashing

    def canonical_json(self, value: Any) -> str:
        """Deterministic sorted-keys serialization. Mirrors the JS verifier."""
        if value is None or not isinstance(value, (dict, list)):
            return json.dumps(value)
        if isinstance(value, list):
            return "[" + ",".join(self.canonical_json(v) for v in value) + "]"
        keys = sorted(value.keys())
        return (
            "{"
            + ",".join(
                json.dumps(k) + ":" + self.canonical_json(value[k]) for k in keys
            )
            + "}"
        )

    def compute_hash(self, prev_hash_hex: str, row_without_hash: Dict[str, Any]) -> str:
        """SHA256( salt || prev_hash_bytes || canonical_json(row) )."""
        digest = hashlib.sha256()
        digest.update(self.salt.encode("utf-8"))
        digest.update(bytes.fromhex(prev_hash_hex))
        digest.update(self.canonical_json(row_without_hash).encode("utf-8"))
        return digest.hexdigest()

    # --------------------------------------------------------------- storage

    def _load(self) -> Dict[str, Any]:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                raise ReceiptError(f"chain unreadable at {self.path}: {exc}") from exc
        return {"salt": self.salt, "rows": []}

    def _save(self, chain: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(chain, indent=2))
        os.replace(tmp, self.path)  # atomic: a crash cannot leave a half-written chain

    # ---------------------------------------------------------------- append

    def append(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """Append one receipt. Values are stringified so hashing is language-stable."""
        with self._lock:
            chain = self._load()
            rows = chain["rows"]
            prev_hash = rows[-1]["hash"] if rows else GENESIS_PREV

            # Key order does not matter to the hash (canonical_json sorts), but a
            # deterministic layout keeps human diffs readable.
            ordered: Dict[str, Any] = {"seq": len(rows), "prev_hash": prev_hash}
            # Every field value is a string before hashing, per the design note above.
            for key in sorted(k for k in fields if k not in ("seq", "prev_hash")):
                ordered[key] = stable_str(fields[key])
            ordered["hash"] = self.compute_hash(prev_hash, ordered)
            rows.append(ordered)
            chain["rows"] = rows
            self._save(chain)
            return ordered

    # ---------------------------------------------------------------- verify

    def verify(self) -> Dict[str, Any]:
        """Re-walk the chain offline and confirm integrity. No network, no SDK."""
        chain = self._load()
        salt_ok = chain.get("salt") == self.salt
        rows = sorted(chain["rows"], key=lambda r: r["seq"])
        prev = GENESIS_PREV
        for row in rows:
            without_hash = {k: v for k, v in row.items() if k != "hash"}
            expected = self.compute_hash(prev, without_hash)
            if row.get("prev_hash") != prev or row.get("hash") != expected:
                return {
                    "ok": False,
                    "count": len(rows),
                    "broken_seq": row.get("seq"),
                    "salt_ok": salt_ok,
                }
            prev = row["hash"]
        return {"ok": True, "count": len(rows), "broken_seq": None, "salt_ok": salt_ok}

    def rows(self) -> List[Dict[str, Any]]:
        return self._load()["rows"]

    def reset(self) -> None:
        with self._lock:
            if self.path.exists():
                self.path.unlink()


# --------------------------------------------------------- benchmark receipts


def receipt_run_manifest(
    chain: ReceiptChain, manifest: Dict[str, Any]
) -> Dict[str, Any]:
    """Receipt the thing that defines the run: model, provider, budget, world hash, SHAs."""
    return chain.append({"kind": "run_manifest", **manifest})


def receipt_task_grade(
    chain: ReceiptChain, arm: str, task_id: str, passed: bool, reason: str
) -> Dict[str, Any]:
    """Receipt one graded task. The hash covers the verdict, not just the answer."""
    return chain.append(
        {
            "kind": "task_grade",
            "arm": str(arm),
            "task_id": str(task_id),
            "passed": "true" if passed else "false",
            "reason": str(reason),
        }
    )


def verify_receipt_chain(path: Path) -> Dict[str, Any]:
    """Offline verification entry point, safe to hand to an independent scorer."""
    return ReceiptChain(path).verify()


def render_verification(chain_path: Path) -> Tuple[bool, str]:
    """Human-readable PASS/FAIL line for a receipt chain. No colour, no decoration."""
    result = verify_receipt_chain(chain_path)
    if result["ok"]:
        return True, f"receipts OK — {result['count']} rows, chain intact"
    return False, (
        f"receipts BROKEN — {result['count']} rows, "
        f"first break at seq {result['broken_seq']}"
    )
