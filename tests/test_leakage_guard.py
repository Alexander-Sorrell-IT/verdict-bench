"""The leakage guard on the real world: zero findings, and still sharp.

A.6's verify, as a regression test: the guard that invalidated the committed
25/25 run returns ZERO findings on the rebuilt world's artifacts - and a gold
value planted outside the task's own evidence is still caught, so the exemption
that makes the zero possible is exactly the task's evidence and nothing wider.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))

# Bare import, per setup.cfg: runner/ is on mypy_path, and resolving bench.py
# under both "bench" and "runner.bench" is the duplicate-module error.
from bench import (  # noqa: E402
    check_sandbox_for_gold,
    leakage_check,
    load_gold,
    load_manifest,
)
from world.paths import FILES_DIR, GOLD_PATH, MANIFEST_PATH  # noqa: E402


def _world_inputs():
    gold = load_gold(GOLD_PATH)
    manifest = load_manifest(MANIFEST_PATH)
    return manifest, gold


def test_the_committed_world_produces_zero_findings():
    manifest, gold = _world_inputs()
    findings = leakage_check(
        manifest, FILES_DIR, gold, prompt_of=lambda task: task["description"]
    )
    assert findings == [], findings


def test_a_gold_value_outside_the_evidence_is_still_a_finding(tmp_path):
    """The exemption is exactly the task's own file. The same value in any other
    file is a finding - the guard stays sharp."""
    _manifest, gold = _world_inputs()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "notes.txt").write_text("the answer is value_2022\n")
    findings = check_sandbox_for_gold(sandbox, gold, manifest={})
    assert any("value_2022" in f for f in findings)


def test_a_gold_hash_in_the_sandbox_is_a_finding(tmp_path):
    _manifest, gold = _world_inputs()
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    gold_hash = next(iter(gold.values()))["gold_hash"]
    (sandbox / "leaked.txt").write_text(gold_hash + "\n")
    findings = check_sandbox_for_gold(sandbox, gold, manifest={})
    assert any("a gold hash" in f for f in findings)


def test_the_manifest_and_prompts_are_clean():
    manifest, gold = _world_inputs()
    from bench import check_manifest_for_leaks, check_task_for_leaks  # noqa: B018

    assert check_manifest_for_leaks(manifest) == []
    for task in manifest["tasks"]:
        assert check_task_for_leaks(task) == []
