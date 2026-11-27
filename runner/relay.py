#!/usr/bin/env python3
# verdict-bench runner/relay.py
#
# The shared model relay, ABSORBED from GitHub verdict-bench (Nov 20, B.1's
# corpus) with the domain glue rewritten for this repo:
#
#   * throttle - one global rate limit for every arm, so the benchmark stays
#     under the provider's quota instead of each arm competing for it;
#   * retry 429/5xx with backoff BEFORE any byte reaches the harness, so no
#     arm is penalised for its own retry policy (FREETIER's 429s are noted in
#     spec/providers.json - this is the machinery that survives them);
#   * streams SSE through unchanged;
#   * one log line per call - run evidence, never the key.
#
# Rewritten glue: the key never lives in this repo. It resolves from the
# environment through harness/keys.py (verdict-bench's own resolution), or from an
# operator-side --key-file; the relay is the ONLY place the real key exists,
# so arms point at it with a placeholder and the key never reaches an arm.
# LOCAL ONLY: binds 127.0.0.1 unless told otherwise.
#
#   python -m runner.relay --upstream https://integrate.api.nvidia.com/v1 --rpm 35
#
# First job is a SMOKE TEST, never a graded run.

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

import httpx

BENCH_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR / "harness"))

from harness.keys import (  # noqa: E402
    default_provider_id,
    load_providers,
    resolve_api_key,
)

RETRYABLE = (429, 500, 502, 503, 504)


def _resolve_key(explicit_file: Optional[Path]) -> str:
    """The real key, resolved once. Loudly refuses if there is none."""
    if explicit_file:
        key = explicit_file.read_text().strip()
        if not key:
            raise SystemExit(f"relay: key file {explicit_file} is empty")
        return key
    providers = load_providers(BENCH_DIR / "spec" / "providers.json")
    provider_id = default_provider_id(BENCH_DIR / "spec" / "providers.json")
    if not provider_id or provider_id not in providers:
        raise SystemExit(
            "relay: no default provider in spec/providers.json; "
            "pass --key-file or set the provider's env key"
        )
    key = resolve_api_key(providers[provider_id])
    if not key:
        raise SystemExit(
            f"relay: no key resolved for provider {provider_id!r}; "
            "set the env key or pass --key-file"
        )
    return key


class Relay:
    """The throttle, the retry policy, and the log. One per process."""

    def __init__(self, args: argparse.Namespace, key: str) -> None:
        self.upstream = args.upstream.rstrip("/")
        self.key = key
        self.rpm = args.rpm
        self.max_tries = args.max_tries
        self.log_path = Path(args.log)
        self.client = httpx.Client(timeout=httpx.Timeout(600, connect=30))
        self._lock = threading.Lock()
        self._stamps: list = []

    def throttle(self) -> None:
        """Block until this call fits the global rpm. Arms share one budget."""
        while True:
            with self._lock:
                now = time.time()
                while self._stamps and now - self._stamps[0] > 60:
                    self._stamps.pop(0)
                if len(self._stamps) < self.rpm:
                    self._stamps.append(now)
                    return
                wait = 60 - (now - self._stamps[0]) + 0.05
            time.sleep(wait)

    def forward(self, method: str, path: str, body: Optional[bytes], accept: str):
        """Forward one call. Retries 429/5xx before any byte reaches the arm."""
        url = self.upstream + path.replace("/v1", "", 1)
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Accept": accept,
        }
        tries = 0
        while True:
            tries += 1
            self.throttle()
            try:
                request = self.client.build_request(
                    method, url, headers=headers, content=body
                )
                response = self.client.send(request, stream=True)
            except httpx.HTTPError as exc:
                if tries < self.max_tries:
                    time.sleep(min(60, 2**tries))
                    continue
                return None, tries, f"upstream unreachable: {exc}"
            if response.status_code in RETRYABLE and tries < self.max_tries:
                response.read()
                response.close()
                time.sleep(min(60, 2**tries))
                continue
            return response, tries, ""
        # unreachable; the loop returns on every path


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    relay: Optional[Relay] = None
    log_handle: Any = None

    def log_message(self, *args: Any) -> None:
        pass

    def _go(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if method == "POST" and length else None
        assert self.relay is not None
        t0 = time.time()
        response, tries, failure = self.relay.forward(
            method, self.path, body, self.headers.get("Accept", "*/*")
        )
        resp_bytes = 0
        if response is None:
            self.send_response(502)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(response.status_code)
            for name, value in response.headers.items():
                if name.lower() not in (
                    "content-length",
                    "transfer-encoding",
                    "connection",
                    "content-encoding",
                ):
                    self.send_header(name, value)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in response.iter_raw():
                if chunk:
                    resp_bytes += len(chunk)
                    self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            response.close()

        if self.log_handle is not None:
            record = {
                "t": time.strftime("%H:%M:%S"),
                "path": self.path,
                "status": response.status_code if response else 502,
                "tries": tries,
                "failure": failure,
                "req_bytes": len(body or b""),
                "resp_bytes": resp_bytes,
                "s": round(time.time() - t0, 2),
            }
            with self.relay._lock:  # noqa: SLF001 - the log lock is the relay's
                self.log_handle.write(json.dumps(record) + "\n")
                self.log_handle.flush()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._go("POST")

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's API
        self._go("GET")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="verdict-bench shared model relay")
    parser.add_argument("--upstream", required=True, help="OpenAI-compatible base URL")
    parser.add_argument(
        "--key-file",
        type=Path,
        default=None,
        help="operator-side key file; otherwise the env key resolves (harness/keys.py)",
    )
    parser.add_argument(
        "--rpm", type=int, default=35, help="global requests per minute"
    )
    parser.add_argument("--max-tries", type=int, default=8)
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1", help="LOCAL ONLY by default")
    parser.add_argument("--log", default="logs/relay.jsonl")
    args = parser.parse_args(argv)

    key = _resolve_key(args.key_file)
    relay = Relay(args, key)
    Handler.relay = relay
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    Handler.log_handle = open(args.log, "a")  # noqa: SIM115 - lives for the process

    print(
        f"relay on {args.host}:{args.port} -> {args.upstream} at {args.rpm} rpm "
        f"(log: {args.log})",
        flush=True,
    )
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nrelay stopped", flush=True)
    finally:
        server.server_close()
        Handler.log_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
