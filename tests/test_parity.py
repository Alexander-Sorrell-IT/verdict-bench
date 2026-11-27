"""Parity: both arms get the SAME client, the SAME budget, the SAME prompt path.

The retired launcher set OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=3200 for one
arm only - 4x the other's output allowance - and the graded numbers were
compared anyway. Parity is now structural: bench builds one client and pushes
the same object into every arm, and the spec names one Budget. These tests
make that enforced rather than assumed.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

from bench import (  # noqa: E402
    Attempt,
    Budget,
    build_client,
    load_arms_spec,
    run_arm,
)
from harness.keys import load_providers  # noqa: E402
from harness.minimal_arm import run, run_gated  # noqa: E402

SPEC = REPO_ROOT / "spec" / "arms.json"
PROVIDERS = REPO_ROOT / "spec" / "providers.json"
MANIFEST = REPO_ROOT / "world" / "world_manifest.json"


class RecordingRunner:
    """Records exactly what bench hands the arm."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.seen: List[Dict[str, Any]] = []

    def __call__(self, task, budget, sandbox, client=None):
        self.seen.append(
            {"task_id": task["task_id"], "budget": budget, "client": client}
        )
        return Attempt(
            task_id=task["task_id"],
            arm=self.name,
            answer="",
            steps=1,
            model_calls=1,
            wall_seconds=0.0,
        )


def _manifest() -> Dict[str, Any]:
    import json

    return json.loads(MANIFEST.read_text())


def _gold() -> Dict[str, Any]:
    import json

    return json.loads(
        (REPO_ROOT / "world" / "private" / "gold_world2.json").read_text()
    )["answers"]


def test_the_spec_names_one_budget_for_every_arm():
    spec = load_arms_spec(SPEC)
    budgets = [tuple(sorted(spec["budget"].items()))]
    assert len(set(budgets)) == 1
    assert set(spec["arms"]) == {"MINIMAL", "GATED"}


def test_bench_passes_the_same_client_and_budget_to_every_arm(tmp_path):
    manifest = _manifest()
    gold = _gold()
    budget = Budget(max_steps=5, max_model_calls=5, max_wall_seconds=30.0)
    shared_client = object()  # identity is the property under test
    (tmp_path / "task.txt").write_text("Do the thing.")

    runners = [RecordingRunner("MINIMAL"), RecordingRunner("GATED")]
    for runner in runners:
        run_arm(
            runner.name,
            runner,
            manifest,
            gold,
            budget,
            tmp_path,
            lambda task: task["description"],
            shared_client,
        )

    for runner in runners:
        assert len(runner.seen) == len(manifest["tasks"])
        for call in runner.seen:
            assert call["client"] is shared_client
            assert call["budget"] is budget


def test_build_client_refuses_a_missing_default_provider(tmp_path):
    import json

    empty = tmp_path / "providers.json"
    empty.write_text(json.dumps({"default_provider": "nope", "providers": {}}))
    try:
        build_client(empty)
    except Exception:
        pass
    else:
        raise AssertionError("a providers file with no usable provider must refuse")


def test_both_arms_share_one_loop_and_differ_only_by_the_gate_engine(tmp_path):
    """Same stub client, same budget, same task: consumption is identical. The
    only variable between MINIMAL and GATED is the injected gate set."""

    class StubClient:
        """Replies from a scripted list."""

        def __init__(self, replies: List[str]) -> None:
            self.replies = list(replies)
            self.calls: List[List[Dict[str, str]]] = []

        @property
        def call_count(self) -> int:
            return len(self.calls)

        @property
        def total_seconds(self) -> float:
            return 0.0

        def complete(
            self, messages: List[Dict[str, str]], max_tokens: int = 500
        ) -> str:
            self.calls.append(messages)
            if not self.replies:
                raise RuntimeError("stub client exhausted")
            return self.replies.pop(0)

    arm_budget = Budget(max_steps=10, max_model_calls=10, max_wall_seconds=60.0)
    (tmp_path / "data.conf").write_text("secret_key_99 = value_2022\n")
    task = {"task_id": "T-1", "family": "unknown", "description": "Find the value."}

    replies_minimal = [
        "cat data.conf",
        '{"value": "value_2022"}',
    ]
    replies_gated = list(replies_minimal)

    attempt_minimal = run(
        task, arm_budget, str(tmp_path), StubClient(list(replies_minimal))
    )
    attempt_gated = run_gated(
        task, arm_budget, str(tmp_path), StubClient(list(replies_gated))
    )

    assert attempt_minimal.steps == attempt_gated.steps
    assert attempt_minimal.model_calls == attempt_gated.model_calls
    assert attempt_minimal.answer == attempt_gated.answer


def test_the_model_client_has_no_silent_model_default():
    from harness.model import ModelClient, ModelError

    try:
        ModelClient(base_url="http://127.0.0.1:1/v1", api_key="k", model="")
    except ModelError:
        pass
    else:
        raise AssertionError("a model client without an explicit model id must refuse")


def test_the_providers_registry_names_the_live_route():
    providers = load_providers(PROVIDERS)
    nvidia = providers["nvidia"]
    assert nvidia.base_url == "https://integrate.api.nvidia.com/v1"
    assert nvidia.default_model == "nvidia/nemotron-3-ultra-550b-a55b"
    assert "NVIDIA_API_KEY" in nvidia.env_key
