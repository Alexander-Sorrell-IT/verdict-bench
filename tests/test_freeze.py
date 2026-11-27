"""The freeze (E.1): the run is refused if the world or the split changed under
it. A legitimate rebuild does not trip it (B.6/B.2 determinism) - a real edit
does.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))

from freeze import FreezeError, compute_freeze, enforce_freeze  # noqa: E402
from world.paths import FILES_DIR, MANIFEST_PATH  # noqa: E402

SPLIT = REPO_ROOT / "spec" / "split.json"


def test_a_second_check_against_the_same_world_is_frozen_ok(tmp_path):
    record = tmp_path / "frozen.json"
    first = enforce_freeze(SPLIT, record_path=record)
    assert first["status"] == "frozen-now"
    second = enforce_freeze(SPLIT, record_path=record)
    assert second["status"] == "frozen-ok"


def test_a_changed_world_file_refuses_the_run(tmp_path):
    record = tmp_path / "frozen.json"
    enforce_freeze(SPLIT, record_path=record)
    victim = sorted(FILES_DIR.glob("*"))[0]
    original = victim.read_bytes()
    try:
        victim.write_bytes(original + b"tampered\n")
        with pytest.raises(FreezeError) as exc:
            enforce_freeze(SPLIT, record_path=record)
        assert "world_files_sha256" in str(exc.value)
    finally:
        victim.write_bytes(original)


def test_a_changed_manifest_refuses_the_run(tmp_path):
    record = tmp_path / "frozen.json"
    enforce_freeze(SPLIT, record_path=record)
    original = MANIFEST_PATH.read_bytes()
    try:
        MANIFEST_PATH.write_bytes(original + b" ")
        with pytest.raises(FreezeError):
            enforce_freeze(SPLIT, record_path=record)
    finally:
        MANIFEST_PATH.write_bytes(original)


def test_a_changed_split_refuses_the_run(tmp_path):
    record = tmp_path / "frozen.json"
    enforce_freeze(SPLIT, record_path=record)
    split_copy = tmp_path / "split.json"
    frozen_split = json.loads(SPLIT.read_text())
    frozen_split["dev"] = frozen_split["dev"][:-1]  # drop one dev task
    split_copy.write_text(json.dumps(frozen_split, indent=2))
    with pytest.raises(FreezeError) as exc:
        enforce_freeze(split_copy, record_path=record)
    assert "split_sha256" in str(exc.value)


def test_a_rebuilt_world_does_not_trip_the_freeze(tmp_path):
    """B.6/B.2's determinism is what makes the freeze meaningful: a rebuild
    reproduces the bytes, so only a real edit refuses."""
    from world.build_world import build_world

    record = tmp_path / "frozen.json"
    enforce_freeze(SPLIT, record_path=record)
    build_world()  # rewrites the world in place, byte-identically
    try:
        assert enforce_freeze(SPLIT, record_path=record)["status"] == "frozen-ok"
    finally:
        assert compute_freeze(SPLIT)  # leave the record computable
