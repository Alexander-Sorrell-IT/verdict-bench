"""World gates — a clean world passes, a contaminated one is refused.

These gates are what the last benchmark did not have. Against the committed world the
live run blocks on exactly one gate (`families` in the manifest) - the "five findings"
figure was a fixture-only layout and was corrected in REMAINING_WORK.md. That one gate
is what A.1 removes.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from world import gates as world_gates  # noqa: E402


HASH = "a" * 64


@pytest.fixture
def layout(tmp_path):
    """A clean two-directory layout: gold world, separate sandbox."""
    world_dir = tmp_path / "world"
    sandbox_dir = tmp_path / "sandbox"
    (world_dir / "world_files").mkdir(parents=True)
    sandbox_dir.mkdir(parents=True)

    (world_dir / "world_files" / "config_51.conf").write_text("k = value_9876\n")
    (sandbox_dir / "config_51.conf").write_text("k = value_9876\n")

    (world_dir / "world_manifest.json").write_text(
        json.dumps(
            {
                "world_id": "w",
                "seed": 1,
                "tasks": [
                    {
                        "task_id": "W2-000",
                        "description": "Find the value.",
                    }
                ],
            }
        )
    )
    (world_dir / "private").mkdir(parents=True)
    # A shippable world carries a real solver - its absence is blocking (A.4).
    (world_dir / "independent_solver.py").write_text(
        "def solve(task):\n    return read_public_files(task)\n"
    )
    (world_dir / "private" / "gold_world2.json").write_text(
        json.dumps(
            {
                "answers": {
                    "W2-000": {
                        "gold_hash": HASH,
                        "gold_answer": {"value": "value_9876"},
                    }
                }
            }
        )
    )
    return world_dir, sandbox_dir


def _run(layout):
    results, blocking = world_gates.check_world(layout[0], layout[1])
    return {r["name"]: r for r in results}, blocking


def _list(layout):
    """The raw result list, for report() which iterates records not names."""
    results, _blocking = world_gates.check_world(layout[0], layout[1])
    return results


def test_a_clean_world_passes(layout):
    results, blocking = _run(layout)
    assert blocking == [], [n for n, r in results.items() if r["status"] == "FAIL"]
    assert results["separation:gold-outside-sandbox"]["status"] == "PASS"
    assert results["leak:no-answer-key-in-public-tree"]["status"] == "PASS"


def test_gold_inside_the_sandbox_is_blocking(layout):
    """The world's own gold may stay put; a COPY of it in the agent's tree may not."""
    world_dir, sandbox_dir = layout
    (sandbox_dir / "gold_world2.json").write_text('{"answers": {}}')
    _results, blocking = _run(layout)
    assert any(n.startswith("separation:no-gold-filename") for n in blocking)


def test_a_derivable_value_in_a_public_file_is_not_a_leak(layout):
    """The answer must be derivable from public data - that is the task, not a leak."""
    world_dir, sandbox_dir = layout
    (sandbox_dir / "notes.txt").write_text("the value is value_9876\n")
    _results, blocking = _run(layout)
    assert "leak:no-answer-key-in-public-tree" not in blocking


def test_the_answer_key_in_a_public_file_is_blocking(layout):
    world_dir, sandbox_dir = layout
    gold = (world_dir / "private" / "gold_world2.json").read_text()
    (sandbox_dir / "leaked.txt").write_text(gold)
    _results, blocking = _run(layout)
    assert "leak:no-answer-key-in-public-tree" in blocking


def test_a_gold_hash_in_a_public_file_is_blocking(layout):
    world_dir, sandbox_dir = layout
    (sandbox_dir / "leaked.txt").write_text("a" * 64 + "\n")
    _results, blocking = _run(layout)
    assert "leak:no-answer-key-in-public-tree" in blocking


def test_the_task_taxonomy_in_a_manifest_is_blocking(layout):
    world_dir, sandbox_dir = layout
    manifest = json.loads((world_dir / "world_manifest.json").read_text())
    manifest["families"] = ["config-parsing", "log-pattern-detection"]
    (world_dir / "world_manifest.json").write_text(json.dumps(manifest))
    _results, blocking = _run(layout)
    assert "manifest:no-leaked-keys" in blocking


def test_an_answer_formula_in_a_prompt_is_blocking(layout):
    world_dir, sandbox_dir = layout
    manifest = json.loads((world_dir / "world_manifest.json").read_text())
    manifest["tasks"][0][
        "description"
    ] = "MANDATORY COMMANDS FOR EACH TASK TYPE (copy exactly): sha256sum world_files/*.bin"
    (world_dir / "world_manifest.json").write_text(json.dumps(manifest))
    _results, blocking = _run(layout)
    assert any(n.startswith("prompt:no-answer-formula") for n in blocking)


def test_a_grader_that_calls_a_model_is_blocking(layout):
    world_dir, sandbox_dir = layout
    (world_dir / "grader.py").write_text(
        "import requests\n"
        "def grade_world(results_path, gold_path=None):\n"
        "    requests.post('http://127.0.0.1:11434/v1/chat/completions')\n"
    )
    _results, blocking = _run(layout)
    assert "grader:no-llm-judge" in blocking


def test_a_stubbed_solver_is_blocking(layout):
    world_dir, sandbox_dir = layout
    (world_dir / "independent_solver.py").write_text(
        "def solve():\n    solved = False  # placeholder\n    return solved\n"
    )
    _results, blocking = _run(layout)
    assert "solver:not-a-stub" in blocking


def test_a_deleted_verifier_blocks_the_world(layout):
    """Deleting the verifier fails the world, not just the run: silence is not
    a pass, so the missing solver is a blocking gate."""
    world_dir, sandbox_dir = layout
    (world_dir / "independent_solver.py").unlink()
    _results, blocking = _run(layout)
    assert "solver:exists" in blocking


def test_gold_sitting_in_the_public_tree_is_blocking(layout):
    """The gap A.3 closes: gold BESIDE the manifest, not in the sandbox. A
    sandbox-only scan passes a directory that literally contains the answer key."""
    world_dir, sandbox_dir = layout
    gold = (world_dir / "private" / "gold_world2.json").read_text()
    (world_dir / "gold_world2.json").write_text(gold)
    _results, blocking = _run(layout)
    assert "separation:no-gold-filename:gold" in blocking
    assert "leak:no-answer-key-in-public-tree" in blocking


def test_gold_under_an_innocent_name_in_the_public_tree_is_blocking(layout):
    """Filename markers miss a renamed key; the canary scan does not - the file's
    CONTENT is the answer key whatever it is called."""
    world_dir, sandbox_dir = layout
    gold = (world_dir / "private" / "gold_world2.json").read_text()
    (world_dir / "answers_v2.json").write_text(gold)
    _results, blocking = _run(layout)
    assert "leak:no-answer-key-in-public-tree" in blocking


def test_a_gold_path_pointing_into_the_public_tree_is_blocking(layout):
    """The exact-path gate: gold RESOLVED inside the agent-visible tree, which is
    what a run misconfigured with a root-level gold path looks like."""
    world_dir, sandbox_dir = layout
    moved = world_dir / "gold_world2.json"
    moved.write_text((world_dir / "private" / "gold_world2.json").read_text())
    results, blocking = world_gates.check_world(world_dir, sandbox_dir, moved)
    assert "separation:gold-outside-public-tree" in blocking
    by_name = {r["name"]: r for r in results}
    assert by_name["separation:gold-outside-public-tree"]["status"] == "FAIL"


def test_the_withheld_private_root_is_not_the_public_tree(layout):
    """The exclusion that makes the scan correct: private/ gold is allowed to
    exist - it is where gold MUST live."""
    world_dir, sandbox_dir = layout
    results, blocking = _run(layout)
    assert blocking == []
    assert results["separation:gold-outside-public-tree"]["status"] == "PASS"


def test_family_in_a_manifest_task_is_blocking(layout):
    """The singular leaks the taxonomy even after the root list is gone."""
    world_dir, sandbox_dir = layout
    manifest = json.loads((world_dir / "world_manifest.json").read_text())
    manifest["tasks"][0]["family"] = "config-parsing"
    (world_dir / "world_manifest.json").write_text(json.dumps(manifest))
    _results, blocking = _run(layout)
    assert "manifest:task-clean:W2-000" in blocking


def test_the_report_states_what_it_cannot_check(layout):
    results = _list(layout)
    text = world_gates.report(results, [])
    assert "what these gates CANNOT check" in text
    assert "NOT a claim that the benchmark is fair" in text
    assert "may not be used" not in text


def test_exit_code_is_nonzero_on_a_blocked_world(layout):
    world_dir, sandbox_dir = layout
    (sandbox_dir / "gold_world2.json").write_text("{}")
    assert world_gates.main([str(world_dir), str(sandbox_dir)]) == 1


def test_exit_code_is_zero_on_a_clean_world(layout):
    assert world_gates.main([str(layout[0]), str(layout[1])]) == 0
