#!/usr/bin/env python3
# verdict-bench runner/freeze.py
#
# The run freeze (E.1): the world and the split are hashed BEFORE any arm runs,
# and any change aborts the run. Not a warning - an abort.
#
# The record is committed (spec/frozen_run.json) and deterministic: the world
# rebuilt byte-for-byte (B.6) plus the split rebuilt byte-for-byte (B.2), so a
# legitimate rebuild reproduces the freeze and only a real edit refuses it.

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))

from world.paths import (  # noqa: E402
    FILES_DIR,
    FREEZE_PATH,
    GOLD_PATH,
    MANIFEST_PATH,
)

FROZEN_RECORD = BENCH_DIR / "spec" / "frozen_run.json"
RULE_VERSION = "1"


def _hash_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _hash_tree(root: Path) -> str:
    """Every file under root, hashed by sorted relative path - order-stable."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda p: str(p.relative_to(root))):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def compute_freeze(split_path: Optional[Path] = None) -> Dict[str, Any]:
    """The freeze record: what the run is allowed to be.

    Hashes only, never a path: an absolute path in the record would refuse
    every relocated checkout, and portability is a rule this repo lives by.
    """
    world_files = _hash_tree(FILES_DIR)
    record: Dict[str, Any] = {
        "rule_version": RULE_VERSION,
        "manifest_sha256": _hash_file(MANIFEST_PATH),
        "gold_sha256": _hash_file(GOLD_PATH),
        "world_files_sha256": world_files,
        "world_freeze_sha256": _hash_file(FREEZE_PATH) if FREEZE_PATH.is_file() else "",
    }
    if split_path is not None and Path(split_path).is_file():
        record["split_sha256"] = _hash_file(Path(split_path))
    return record


def enforce_freeze(
    split_path: Optional[Path] = None, record_path: Path = FROZEN_RECORD
) -> Dict[str, Any]:
    """Compare the live world+split against the committed freeze.

    Returns the record. A missing record is written (the first run) and said
    so; a MISMATCH is a refused run.
    """
    live = compute_freeze(split_path)
    if not Path(record_path).is_file():
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(live, indent=2, sort_keys=True) + "\n")
        return {"status": "frozen-now", "record": live}
    frozen = json.loads(Path(record_path).read_text())
    if frozen == live:
        return {"status": "frozen-ok", "record": live}
    changed = {
        key: {"frozen": frozen.get(key), "live": live.get(key)}
        for key in sorted(set(frozen) | set(live))
        if frozen.get(key) != live.get(key)
    }
    raise FreezeError(f"the world or the split changed under the run: {changed}")


class FreezeError(RuntimeError):
    """The run is refused: the world it was staged against is not the world
    on disk."""


def main(argv: Optional[list] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else [])
    split_path = Path(argv[0]) if argv else BENCH_DIR / "spec" / "split.json"
    try:
        outcome = enforce_freeze(split_path)
    except FreezeError as exc:
        print(f"FREEZE REFUSED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
