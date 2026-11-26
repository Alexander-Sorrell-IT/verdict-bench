#!/usr/bin/env python3
# verdict-bench world/paths.py
#
# The two roots of a verdict-bench world, defined once:
#
#   PUBLIC_ROOT  - everything an arm may read: world_files/ and world_manifest.json
#                  sit directly under it. Nothing gold-derived may exist anywhere
#                  under it except the one withheld subtree below.
#   PRIVATE_ROOT - the withheld root: the answer key (gold) and the freeze record.
#                  An arm's sandbox never contains it; the grader receives the gold
#                  path as an ARGUMENT (world/gates.py grades that signature).
#
# The invariant every other module relies on: the agent-visible tree is PUBLIC_ROOT
# minus PRIVATE_ROOT. A per-file hardcoded path is how the last world leaked its
# gold beside its manifest - the convention lives here or it does not hold.

from __future__ import annotations

from pathlib import Path

WORLD_DIR = Path(__file__).resolve().parent
REPO_ROOT = WORLD_DIR.parent

# The agent-visible root. PRIVATE_ROOT is the one subtree under it that is withheld.
PUBLIC_ROOT = WORLD_DIR
PRIVATE_ROOT = WORLD_DIR / "private"

PRIVATE_DIRNAME = "private"
GOLD_FILENAME = "gold_world2.json"
FREEZE_FILENAME = "FREEZE_world2.json"

FILES_DIR = WORLD_DIR / "world_files"
MANIFEST_PATH = WORLD_DIR / "world_manifest.json"

GOLD_PATH = PRIVATE_ROOT / GOLD_FILENAME
FREEZE_PATH = PRIVATE_ROOT / FREEZE_FILENAME
