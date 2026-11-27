"""The world rebuilds byte-for-byte.

E.1 freezes the world hash and aborts on change, so a benign rebuild must not
change it - and "someone else regenerates it from committed code" only means
something if the regeneration is identical. Gold was always reproducible; the
manifest and freeze carried clock timestamps until B.6.
"""

import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from world.build_world import build_world  # noqa: E402
from world.paths import FREEZE_PATH, GOLD_PATH, MANIFEST_PATH  # noqa: E402

ARTIFACTS = (MANIFEST_PATH, GOLD_PATH, FREEZE_PATH)


def _digests() -> dict:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in ARTIFACTS
    }


def test_a_rebuild_reproduces_every_artifact():
    before = _digests()
    build_world()  # rewrites the world in place - deterministically
    after = _digests()
    assert (
        before == after
    ), f"rebuild changed: {set(before.items()) ^ set(after.items())}"


def test_the_world_timestamp_is_seed_derived_not_clock_derived():
    import json

    from world.build_world import _world_timestamp

    assert _world_timestamp(20261120) == _world_timestamp(20261120)
    assert _world_timestamp(20261120) != _world_timestamp(20260101)
    manifest = json.loads(MANIFEST_PATH.read_text())
    assert manifest["created_at"] == _world_timestamp(manifest["seed"])


def test_the_freeze_hash_matches_the_artifacts():
    import json

    freeze = json.loads(FREEZE_PATH.read_text())
    assert (
        freeze["manifest_hash"]
        == hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
    )
    assert freeze["gold_hash"] == hashlib.sha256(GOLD_PATH.read_bytes()).hexdigest()
