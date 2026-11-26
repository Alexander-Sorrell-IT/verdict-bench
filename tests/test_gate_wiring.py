"""The gates are binding: a world that fails them cannot reach an arm.

Before A.4 the world gates existed and nothing called them - a contaminated
world passed every check by never being checked. These tests run the refusals
for real: bench.py and run_world.py must exit before any arm loads.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))

# Bare import, per setup.cfg: resolving bench.py under both "bench" and
# "runner.bench" is the duplicate-module error mypy refuses.
from bench import BenchmarkError, enforce_world_gates  # noqa: E402
from world.paths import GOLD_PATH, WORLD_DIR  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def world_copy(tmp_path):
    """A copy of the committed world: public tree, private gold, solver."""
    dest = tmp_path / "world"
    shutil.copytree(WORLD_DIR, dest, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def test_the_committed_world_passes_the_runner_enforcement(world_copy):
    enforce_world_gates(world_copy, world_copy / "world_files", GOLD_PATH)


def test_gold_beside_the_manifest_refuses_the_run(world_copy):
    shutil.copy(GOLD_PATH, world_copy / "gold_world2.json")
    with pytest.raises(BenchmarkError) as exc:
        enforce_world_gates(world_copy, world_copy / "world_files", GOLD_PATH)
    assert "no-gold-filename" in str(exc.value)


def test_a_deleted_verifier_refuses_the_run(world_copy):
    (world_copy / "independent_solver.py").unlink()
    with pytest.raises(BenchmarkError) as exc:
        enforce_world_gates(world_copy, world_copy / "world_files", GOLD_PATH)
    assert "solver:exists" in str(exc.value)


def test_bench_refuses_a_blocked_world_with_exit_1(world_copy, tmp_path):
    """The runner, end to end: exit 1 before any arm is loaded."""
    shutil.copy(GOLD_PATH, world_copy / "gold_world2.json")
    proc = subprocess.run(
        [
            sys.executable,
            "runner/bench.py",
            "--spec",
            "spec/arms.json",
            "--manifest",
            str(world_copy / "world_manifest.json"),
            "--gold",
            str(world_copy / "private" / "gold_world2.json"),
            "--sandbox",
            str(world_copy / "world_files"),
            "--out",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 1
    assert "RUN REFUSED" in proc.stdout + proc.stderr


def test_run_world_refuses_a_blocked_world_with_exit_1(world_copy, tmp_path):
    """The legacy runner, end to end: exit 1 before any arm, no model call."""
    shutil.copy(GOLD_PATH, world_copy / "gold_world2.json")
    proc = subprocess.run(
        [
            sys.executable,
            "runner/run_world.py",
            "--world",
            str(world_copy / "world_manifest.json"),
            "--arms",
            "AF2",
            "--output-dir",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert proc.returncode == 1
    assert "RUN REFUSED" in proc.stdout + proc.stderr
