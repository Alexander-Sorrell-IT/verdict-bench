"""Operator mode (G): any key, any model, both arms, one budget, receipts.

G.1 - one client per config, both arms, independent grade, receipts written.
G.2 - the parity invariant: a spec that tries to differentiate the arms is
refused (that is how one arm once got 4x the other's output allowance).
G.3 - a sweep PLANS its receipts; execution is a separate, explicit act.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

from bench import BenchmarkError, build_client, load_arms_spec  # noqa: E402
from operator_mode import parse_config, plan_sweep  # noqa: E402

SPEC = REPO_ROOT / "spec" / "arms.json"
PROVIDERS = REPO_ROOT / "spec" / "providers.json"
MANIFEST = REPO_ROOT / "world" / "world_manifest.json"
GOLD = REPO_ROOT / "world" / "private" / "gold_world2.json"


def test_the_committed_spec_loads_and_names_two_arms():
    spec = load_arms_spec(SPEC)
    assert set(spec["arms"]) == {"MINIMAL", "GATED"}
    assert spec["budget"]["max_model_calls"] == 200


def test_a_per_arm_override_is_refused(tmp_path):
    """G.2: a spec that differentiates the arms is not a spec."""
    bad = tmp_path / "arms.json"
    bad.write_text(
        json.dumps(
            {
                "budget": {
                    "max_steps": 10,
                    "max_model_calls": 10,
                    "max_wall_seconds": 60,
                },
                "arms": {
                    "MINIMAL": "harness.minimal_arm:run",
                    "GATED": {
                        "entrypoint": "harness.minimal_arm:run_gated",
                        "model": "other/model",
                        "max_tokens": 3200,
                    },
                },
            }
        )
    )
    with pytest.raises(BenchmarkError) as exc:
        load_arms_spec(bad)
    assert "per-arm override" in str(exc.value)


def test_a_per_arm_model_key_is_refused(tmp_path):
    bad = tmp_path / "arms.json"
    bad.write_text(
        json.dumps(
            {
                "budget": {
                    "max_steps": 10,
                    "max_model_calls": 10,
                    "max_wall_seconds": 60,
                },
                "arms": {
                    "MINIMAL": "harness.minimal_arm:run",
                    "GATED": {
                        "entrypoint": "harness.minimal_arm:run_gated",
                        "output_token_max": 32000,
                    },
                },
            }
        )
    )
    with pytest.raises(BenchmarkError):
        load_arms_spec(bad)


def test_build_client_takes_an_operator_override_without_a_silent_default():
    client = build_client(PROVIDERS, "nvidia", "nvidia/nemotron-3-ultra-550b-a55b")
    assert client.model == "nvidia/nemotron-3-ultra-550b-a55b"
    assert client.base_url == "https://integrate.api.nvidia.com/v1"
    with pytest.raises(BenchmarkError):
        build_client(PROVIDERS, "no-such-provider")


def test_parse_config_accepts_provider_and_provider_model():
    assert parse_config("nvidia") == ("nvidia", "")
    assert parse_config("nvidia:nvidia/some-model") == ("nvidia", "nvidia/some-model")
    with pytest.raises(BenchmarkError):
        parse_config(":model-only")


def test_a_sweep_plans_one_receipt_chain_per_config():
    plan = plan_sweep(["nvidia:model-a", "groq", "openrouter:model-c"])
    assert len(plan["configs"]) == 3
    chains = {c["receipts"] for c in plan["configs"]}
    rows = {c["rows"] for c in plan["configs"]}
    assert len(chains) == 3 and len(rows) == 3
    assert "execute" in json.dumps(plan)
