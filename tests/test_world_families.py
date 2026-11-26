"""World 2's task families must not overlap World 1's (plan 3.3).

Both sides are READ from committed artifacts, not typed from memory: World 1's
families are what the recorded attempts in results/world1/ actually ran (the
only surviving World-1 evidence), and World 2's are the generator's
TASK_FAMILIES. A family name that collapses the two worlds fails here, and a
family name reaching the agent-visible manifest or data fails here too - an
arm that can see the taxonomy no longer has to discover anything.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from world.build_world import TASK_FAMILIES  # noqa: E402
from world.paths import FILES_DIR, MANIFEST_PATH  # noqa: E402

WORLD1_RESULTS = REPO_ROOT / "results" / "world1"

# The five families the recorded World-1 attempts actually ran. Pinned so a
# silent change to the evidence files fails loudly instead of redefining the
# comparison.
WORLD1_FAMILIES = {
    "contract-compliance",
    "ops-reporting",
    "config-audit",
    "log-forensics",
    "reconciliation",
}


def _world1_families_from_evidence() -> set:
    families = set()
    for path in sorted(WORLD1_RESULTS.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                families.add(json.loads(line)["family"])
    return families


def test_the_recorded_world1_attempts_are_the_five_known_families():
    assert _world1_families_from_evidence() == WORLD1_FAMILIES


def test_world2_families_do_not_overlap_world1():
    overlap = sorted(set(TASK_FAMILIES) & WORLD1_FAMILIES)
    assert not overlap, f"families appear in both worlds: {overlap}"


def test_world2_families_are_five_distinct_names():
    assert len(TASK_FAMILIES) == len(set(TASK_FAMILIES)) == 5


def test_the_agent_visible_manifest_names_no_family():
    text = MANIFEST_PATH.read_text()
    hits = [f for f in TASK_FAMILIES if f in text]
    assert not hits, f"manifest names task families: {hits}"


def test_the_agent_visible_data_names_no_family():
    """File names and contents: the taxonomy must be absent from what the arm reads."""
    for path in sorted(FILES_DIR.iterdir()):
        assert not [f for f in TASK_FAMILIES if f in path.name], path.name
        text = path.read_text(errors="replace")
        hits = [f for f in TASK_FAMILIES if f in text]
        assert not hits, f"{path.name} contains family names: {hits}"
