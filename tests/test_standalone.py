"""verdict-bench is STANDALONE: it carries its own provider registry and key
resolution, and it runs against any OpenAI-compatible endpoint and credential
without another tool installed.

Two things are asserted: the route surface accepts any provider/model/key (not
one hardcoded default), and no shipped source or config references the tool
this repo used to lean on.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "runner"))
sys.path.insert(0, str(REPO_ROOT / "harness"))

from bench import build_client  # noqa: E402
from hypothesis import relay_client  # noqa: E402

PROVIDERS = REPO_ROOT / "spec" / "providers.json"


def test_any_provider_and_model_are_selectable(monkeypatch):
    """Not locked to the registry's default: pick a provider and a model, and
    the client is built for THAT route."""
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    client = relay_client(provider_id="groq", model="llama-3.3-70b-versatile")
    assert client.model == "llama-3.3-70b-versatile"
    assert client.base_url == "https://api.groq.com/openai/v1"
    assert client.api_key == "groq-key"


def test_any_key_env_var_is_accepted(monkeypatch):
    """--key-env: the key can live in the operator's own variable name."""
    monkeypatch.setenv("MY_OWN_KEY_VAR", "my-key")
    client = build_client(PROVIDERS, "nvidia", "some/model", key_env="MY_OWN_KEY_VAR")
    assert client.api_key == "my-key"
    from harness.keys import KeyError_

    with pytest.raises(KeyError_):
        build_client(PROVIDERS, "nvidia", key_env="EMPTY_VAR_THAT_IS_NOT_SET")


def test_an_unknown_provider_names_the_known_ones():
    with pytest.raises(Exception) as exc:
        relay_client(provider_id="no-such-provider")
    assert "nvidia" in str(exc.value) and "groq" in str(exc.value)


def test_your_own_registry_loads(tmp_path, monkeypatch):
    registry = tmp_path / "mine.json"
    registry.write_text(
        """
        {
          "default_provider": "mine",
          "providers": {
            "mine": {
              "base_url": "https://my.endpoint.example/v1",
              "env_key": "MY_ENDPOINT_KEY",
              "default_model": "my-model"
            }
          }
        }
        """
    )
    monkeypatch.setenv("MY_ENDPOINT_KEY", "mine-key")
    client = relay_client(providers_path=registry)
    assert client.base_url == "https://my.endpoint.example/v1"
    assert client.model == "my-model"
    assert client.api_key == "mine-key"


def test_no_shipped_source_or_config_references_the_old_tool():
    """The repo stands alone: the SHIPPED trees (harness/, runner/, score/,
    world/, spec/) mention no external harness by name. History lives in the
    plan docs, not in shipped code. (The tests may name it - that is how the
    absence is enforced.)"""
    offenders: list = []
    for directory in ("harness", "runner", "score", "world", "spec"):
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if path.suffix not in (".py", ".json") or "__pycache__" in path.parts:
                continue
            if "omnicli" in path.read_text(errors="replace").lower():
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], offenders
