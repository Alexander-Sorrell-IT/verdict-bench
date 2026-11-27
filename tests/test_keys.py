"""API-key resolution — this repo's own precedence, any provider, and loud
failure instead of a default. verdict-bench is standalone: no other tool needs
to be installed for a key to resolve."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "runner"))

from harness import keys  # noqa: E402


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No ambient key may leak into these tests."""
    for var in (
        "NVIDIA_API_KEY",
        "NVIDIA_API_KEY".replace("X", "X"),
        "NVAPI_KEY",
        "OPENROUTER_API_KEY",
        "GROQ_API_KEY",
        "GEMINI_API_KEY",
        "VERDICT_BENCH_PROVIDERS",
        "VERDICT_BENCH_KEYS_FILE",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


def _providers(tmp_path, providers):
    path = tmp_path / "providers.json"
    path.write_text(json.dumps({"providers": providers}))
    return path


def test_env_key_wins_over_alt_and_file(tmp_path, monkeypatch):
    path = _providers(
        tmp_path,
        {
            "nvidia": {
                "name": "NVIDIA",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "env_key": "NVIDIA_API_KEY",
                "alt_env_keys": ["NVAPI_KEY"],
                "default_model": "m",
                "requires_auth_for_models": True,
            }
        },
    )
    monkeypatch.setenv("NVIDIA_API_KEY", "  primary-key  ")
    monkeypatch.setenv("NVAPI_KEY", "alt-key")
    (tmp_path / "keys.json").write_text(json.dumps({"nvidia": "file-key"}))
    monkeypatch.setenv("VERDICT_BENCH_KEYS_FILE", str(tmp_path / "keys.json"))

    providers = keys.load_providers(path)
    # whitespace stripped, env first - this repo's order
    assert keys.resolve_api_key(providers["nvidia"]) == "primary-key"


def test_alt_env_key_is_used_when_primary_absent(tmp_path, monkeypatch):
    path = _providers(
        tmp_path,
        {
            "nvidia": {
                "base_url": "https://integrate.api.nvidia.com/v1",
                "env_key": "NVIDIA_API_KEY",
                "alt_env_keys": ["NVAPI_KEY"],
                "requires_auth_for_models": True,
            }
        },
    )
    monkeypatch.setenv("NVAPI_KEY", "alt-key")
    assert keys.resolve_api_key(keys.load_providers(path)["nvidia"]) == "alt-key"


def test_keys_file_supplies_the_key(tmp_path, monkeypatch):
    path = _providers(
        tmp_path,
        {
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "env_key": "GROQ_API_KEY",
            }
        },
    )
    (tmp_path / "keys.json").write_text(json.dumps({"groq": "file-key"}))
    monkeypatch.setenv("VERDICT_BENCH_KEYS_FILE", str(tmp_path / "keys.json"))
    assert keys.resolve_api_key(keys.load_providers(path)["groq"]) == "file-key"


def test_missing_key_raises_rather_than_defaulting(tmp_path):
    path = _providers(
        tmp_path,
        {
            "groq": {
                "base_url": "https://api.groq.com/openai/v1",
                "env_key": "GROQ_API_KEY",
            }
        },
    )
    with pytest.raises(keys.KeyError_) as exc:
        keys.resolve_api_key(keys.load_providers(path)["groq"])
    assert "GROQ_API_KEY" in str(exc.value) and "Not inventing" in str(exc.value)


def test_local_provider_does_not_need_a_key(tmp_path):
    path = _providers(
        tmp_path,
        {
            "ollama": {
                "base_url": "http://127.0.0.1:11434/v1",
                "env_key": "OLLAMA_API_KEY",
            }
        },
    )
    provider = keys.load_providers(path)["ollama"]
    assert keys.needs_key(provider) is False


def test_hosted_provider_needs_a_key(tmp_path):
    path = _providers(
        tmp_path,
        {
            "nvidia": {
                "base_url": "https://integrate.api.nvidia.com/v1",
                "env_key": "NVIDIA_API_KEY",
            }
        },
    )
    assert keys.needs_key(keys.load_providers(path)["nvidia"]) is True


def test_any_openai_compatible_registry_loads_unchanged(tmp_path):
    """Any provider registry with the plain shape loads as-is: your own file,
    your own endpoints, no translation layer and no dependency on this repo's."""
    registry = {
        "default_provider": "groq",
        "providers": {
            "nvidia": {
                "name": "NVIDIA",
                "base_url": "https://integrate.api.nvidia.com/v1",
                "env_key": "NVIDIA_API_KEY",
                "alt_env_keys": ["NVAPI_KEY"],
                "default_model": "meta/llama-3.2-11b",
                "fallback_models": ["a", "b"],
            },
            "cloudflare": {
                "base_url_template": "https://api.cloudflare.com/x/{account_id}/ai/v1",
                "env_key": "CLOUDFLARE_API_TOKEN",
            },
        },
    }
    path = tmp_path / "my-registry.json"
    path.write_text(json.dumps(registry))
    loaded = keys.load_providers(path)
    assert set(loaded) == {"nvidia", "cloudflare"}
    assert loaded["nvidia"].alt_env_keys == ["NVAPI_KEY"]
    assert keys.default_provider_id(path) == "groq"
    # templated url needs an account id, and says so
    with pytest.raises(keys.KeyError_) as exc:
        loaded["cloudflare"].resolve_base_url()
    assert "account_id" in str(exc.value)
    assert (
        loaded["cloudflare"]
        .resolve_base_url(account_id="acct_123")
        .endswith("/acct_123/ai/v1")
    )


def test_describe_never_contains_the_key(tmp_path, monkeypatch):
    path = _providers(
        tmp_path,
        {
            "nvidia": {
                "base_url": "https://integrate.api.nvidia.com/v1",
                "env_key": "NVIDIA_API_KEY",
                "default_model": "m",
            }
        },
    )
    monkeypatch.setenv("NVIDIA_API_KEY", "SECRET-VALUE")
    line = keys.describe(keys.load_providers(path)["nvidia"])
    assert "SECRET-VALUE" not in line and "key set" in line
