#!/usr/bin/env python3
# verdict-bench harness/model.py
#
# One OpenAI-compatible model client, relay-aware. Every arm talks to the model through
# this and nothing else, so "same model" is a property of the harness rather than a
# promise in a results file.
#
# Three rules, each from a failure this project already had:
#   1. The key is never logged, never placed in a receipt row, and never echoed to stderr.
#   2. A call either returns content or raises. There is no path that returns a
#      synthesized placeholder - that is how a run reports success while doing nothing.
#   3. Latency and call count are measured per call, because budget parity is measured,
#      not asserted, and an arm that quietly exceeds its budget must be visible.

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Sequence

DEFAULT_TIMEOUT = 600.0
DEFAULT_RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class ModelError(RuntimeError):
    """Raised when a model call cannot be completed. Never swallowed."""


class ModelCall:
    """What one call cost. This is what budget parity and the receipt chain consume."""

    def __init__(self, seconds: float, status: str, prompt_tokens: int = 0) -> None:
        self.seconds = seconds
        self.status = status
        self.prompt_tokens = prompt_tokens


class ModelClient:
    """OpenAI-compatible chat client. `requests` imported lazily so the module imports
    fast and tests need nothing installed."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        temperature: float = 0.0,
    ) -> None:
        if not base_url:
            raise ModelError(
                "no base_url: a model client without an endpoint is not a client"
            )
        if not model:
            raise ModelError(
                "no model id: 'whatever the default is' is how arms diverge"
            )
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.temperature = temperature

        self.calls: List[ModelCall] = []

    # ------------------------------------------------------------------ public API

    def complete(
        self, messages: Sequence[Dict[str, str]], max_tokens: int = 500
    ) -> str:
        """One chat completion. Returns content, or raises ModelError.

        Messages are the only input: no prompt assembly happens in here, so what the model
        saw is exactly what the caller chose to send, and the leakage guard stays outside
        this class where it can be audited.
        """
        import requests

        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": self.temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Content-Type": "application/json",
            # The key travels in a header only. It is never logged, formatted into an
            # error message, or placed in a receipt row.
            "Authorization": f"Bearer {self.api_key}",
        }
        url = f"{self.base_url}/chat/completions"

        last_error = ""
        for attempt in range(1, self.retries + 1):
            started = time.perf_counter()
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=(30.0, self.timeout),
                )
                elapsed = time.perf_counter() - started

                if response.status_code in RETRY_STATUSES and attempt < self.retries:
                    # Retry before the arm sees the failure, so no arm is penalised for
                    # its own retry policy. Backoff is recorded, not hidden.
                    self.calls.append(
                        ModelCall(elapsed, f"retry_{response.status_code}")
                    )
                    last_error = f"HTTP {response.status_code}"
                    time.sleep(min(30.0, 2.0**attempt))
                    continue

                if response.status_code != 200:
                    self.calls.append(
                        ModelCall(elapsed, f"http_{response.status_code}")
                    )
                    raise ModelError(
                        f"model {self.model!r} returned HTTP {response.status_code} "
                        f"from {self.base_url}"
                    )

                self.calls.append(ModelCall(elapsed, "ok"))
                return _extract_content(response)

            except ModelError:
                raise
            except Exception as exc:  # noqa: BLE001 - network failures must be named
                elapsed = time.perf_counter() - started
                self.calls.append(ModelCall(elapsed, "transport_error"))
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt >= self.retries:
                    break
                time.sleep(min(30.0, 2.0**attempt))

        raise ModelError(
            f"model {self.model!r} unreachable after {self.retries} attempts "
            f"(last: {last_error})"
        )

    # -------------------------------------------------------------- accounting

    @property
    def call_count(self) -> int:
        """Attempts, including retries. An arm cannot pay for its failures by hiding them."""
        return len(self.calls)

    @property
    def total_seconds(self) -> float:
        return round(sum(c.seconds for c in self.calls), 3)

    @property
    def retry_count(self) -> int:
        return sum(1 for c in self.calls if c.status.startswith("retry_"))

    def manifest_fields(self) -> Dict[str, Any]:
        """Everything a run manifest needs about the model. Never the key."""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "temperature": self.temperature,
            "timeout_s": self.timeout,
            "calls": self.call_count,
            "retries": self.retry_count,
            "model_seconds": self.total_seconds,
        }


def _extract_content(response: Any) -> str:
    """Pull content out of an OpenAI-compatible response, or raise.

    An empty completion is a failure, not an empty string to be handed back as an answer.
    """
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise ModelError(f"model response was not JSON: {exc}") from exc

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelError(f"model response had no choices: {_shape(payload)}")

    message = choices[0].get("message") or {}
    content = (message.get("content") or "").strip()
    reasoning = (message.get("reasoning") or "").strip()
    output = content or reasoning
    if not output:
        raise ModelError(
            "model returned an empty completion (content and reasoning both blank)"
        )
    return output


def _shape(payload: Any) -> str:
    """A short, key-free description of a response, for error messages only."""
    if isinstance(payload, dict):
        return "keys=" + ",".join(sorted(payload)[:8])
    return f"type={type(payload).__name__}"
