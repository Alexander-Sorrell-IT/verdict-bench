#!/usr/bin/env python3
# verdict-bench runner/keys.py
#
# API-key resolution, shaped to match OmniCLI so existing provider configs and keys are
# reused rather than duplicated. OmniCLI's ConfigManager.get_api_key order:
#
#     1. the provider's env_key in the process environment
#     2. any of its alt_env_keys
#     3. a keys file (OMNICLI_KEYS_FILE / a verdict-bench equivalent)
#     4. nothing - and it raises rather than inventing a key
#
# Field names mirror OmniCLI's provider entries so ~/.config/omnicli/config.json loads
# unmodified: name, base_url, base_url_template, env_key, alt_env_keys, default_model,
# description, requires_auth_for_models, fallback_models.
#
# No key is ever written to a config, a results file, or a receipt row.

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

# Config discovery, same order OmniCLI uses.
CONFIG_ENV = "VERDICT_BENCH_PROVIDERS"
OMNICLI_CONFIG_ENV = "OMNICLI_CONFIG_DIR"
KEYS_FILE_ENV_VARIANTS = (
    "VERDICT_BENCH_KEYS_FILE",
    "OMNICLI_KEYS_FILE",
    "TRIO_KEYS_FILE",
)

BENCH_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PROVIDERS = BENCH_DIR / "spec" / "providers.json"


class KeyError_(RuntimeError):
    """Raised when a provider cannot supply a key. Never swallowed into a default."""


class Provider:
    """One OpenAI-compatible endpoint, described the way OmniCLI describes them."""

    def __init__(self, provider_id: str, spec: Dict[str, Any]) -> None:
        self.id = provider_id
        self.name: str = spec.get("name", provider_id)
        self.base_url: str = spec.get("base_url", "")
        self.base_url_template: str = spec.get("base_url_template", "")
        self.env_key: Optional[str] = spec.get("env_key")
        self.alt_env_keys: List[str] = list(spec.get("alt_env_keys", []))
        self.default_model: Optional[str] = spec.get("default_model")
        self.description: str = spec.get("description", "")
        self.requires_auth_for_models: bool = bool(
            spec.get("requires_auth_for_models", False)
        )
        self.fallback_models: List[str] = list(spec.get("fallback_models", []))

    def resolve_base_url(self, account_id: str = "") -> str:
        """Interpolate templated base urls (Cloudflare-style {account_id})."""
        if self.base_url:
            return self.base_url
        if not self.base_url_template:
            raise KeyError_(
                f"provider {self.id!r} has no base_url or base_url_template"
            )
        if "{account_id}" in self.base_url_template and not account_id:
            raise KeyError_(f"provider {self.id!r} needs an account_id; none supplied")
        return self.base_url_template.replace("{account_id}", account_id)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Provider({self.id}, base_url={self.base_url or self.base_url_template!r})"
        )


def load_providers(path: Optional[Path] = None) -> Dict[str, Provider]:
    """Load providers from verdict-bench's spec or from an OmniCLI config.

    Accepts either file: OmniCLI's `providers` block has the same shape this reads, so
    ~/.config/omnicli/config.json works without translation.
    """
    config_path = _config_path(path)
    try:
        raw = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise KeyError_(f"provider config unreadable at {config_path}: {exc}") from exc

    block = raw.get("providers")
    if not isinstance(block, dict) or not block:
        raise KeyError_(f"{config_path} has no 'providers' block")
    return {pid: Provider(pid, spec) for pid, spec in block.items()}


def _config_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    explicit = os.environ.get(CONFIG_ENV, "").strip()
    if explicit:
        return Path(explicit)
    omnicli_dir = os.environ.get(OMNICLI_CONFIG_ENV, "").strip()
    if omnicli_dir:
        candidate = Path(omnicli_dir) / "config.json"
        if candidate.exists():
            return candidate
    if DEFAULT_PROVIDERS.exists():
        return DEFAULT_PROVIDERS
    raise KeyError_(
        "no provider config found: set VERDICT_BENCH_PROVIDERS, or OMNICLI_CONFIG_DIR, "
        f"or ship {DEFAULT_PROVIDERS}"
    )


def _keys_file() -> Optional[Path]:
    for var in KEYS_FILE_ENV_VARIANTS:
        value = os.environ.get(var, "").strip()
        if value:
            candidate = Path(value)
            if candidate.exists():
                return candidate
    return None


def resolve_api_key(provider: Provider) -> str:
    """Resolve a key in OmniCLI's order: env_key, alt_env_keys, keys file, then fail.

    A missing key is an error, not a default. "host" or "ollama" style placeholders are
    how a run quietly sends the wrong credential.
    """
    for var in [provider.env_key, *provider.alt_env_keys]:
        if not var:
            continue
        value = os.environ.get(var, "").strip()
        if value:
            return value

    keys_file = _keys_file()
    if keys_file is not None:
        try:
            stored = json.loads(keys_file.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise KeyError_(f"keys file unreadable at {keys_file}: {exc}") from exc
        entry = stored.get(provider.id)
        if isinstance(entry, str) and entry.strip():
            return entry.strip()
        if isinstance(entry, dict) and entry.get("api_key"):
            return str(entry["api_key"]).strip()

    raise KeyError_(
        f"no API key for provider {provider.id!r}: set ${provider.env_key}"
        + (f" (or one of {provider.alt_env_keys})" if provider.alt_env_keys else "")
        + " or supply a keys file. Not inventing one."
    )


def needs_key(provider: Provider) -> bool:
    """Does this provider actually require authentication?

    Local endpoints (127.0.0.1 / localhost) do not, and must not be sent a key: OmniCLI's
    own default base_url is 127.0.0.1, not localhost, so hostname matching alone is wrong.
    """
    if not provider.env_key:
        return False
    url = provider.resolve_base_url()
    if any(host in url for host in ("127.0.0.1", "localhost", "[::1]")):
        return False
    return provider.requires_auth_for_models or True


def describe(provider: Provider) -> str:
    """One line for a run manifest. Never contains the key."""
    has_key = "key set" if _key_available(provider) else "NO KEY"
    return (
        f"{provider.id}: base_url={provider.resolve_base_url()} "
        f"model={provider.default_model} ({has_key})"
    )


def _key_available(provider: Provider) -> bool:
    try:
        resolve_api_key(provider)
        return True
    except KeyError_:
        return False


def default_provider_id(path: Optional[Path] = None) -> Optional[str]:
    """The config's default_provider, if it names one."""
    config_path = _config_path(path)
    try:
        return json.loads(config_path.read_text()).get("default_provider")
    except (OSError, json.JSONDecodeError):
        return None
