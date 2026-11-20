"""Shared model relay for ALL arms (identical treatment). Forwards OpenAI-compatible calls to
the upstream, keeps the whole benchmark under the provider's rate limit with one global
throttle, and retries 429/5xx with backoff BEFORE any byte reaches the harness, so no harness
is penalised for its own retry policy. Streams SSE through unchanged. Logs one line per call.
  python runner/relay.py --upstream https://integrate.api.nvidia.com/v1 --key-file secrets/nvidia.key --rpm 35
"""
import argparse, json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import httpx

A = None; LOCK = threading.Lock(); STAMPS = []; LOG = None; CLIENT = None


def throttle():
    while True:
        with LOCK:
            now = time.time()
            while STAMPS and now - STAMPS[0] > 60:
                STAMPS.pop(0)
            if len(STAMPS) < A.rpm:
                STAMPS.append(now); return
            wait = 60 - (now - STAMPS[0]) + 0.05
        time.sleep(wait)


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _go(self, method):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) if method == "POST" else None
        url = A.upstream.rstrip("/") + self.path.replace("/v1", "", 1)
        hdr = {"Authorization": f"Bearer {A.key}", "Content-Type": "application/json", "Accept": self.headers.get("Accept", "*/*")}
        t0 = time.time(); tries = 0
        while True:
            tries += 1; throttle()
            try:
                req = CLIENT.build_request(method, url, headers=hdr, content=body)
                r = CLIENT.send(req, stream=True)
            except httpx.HTTPError as e:
                if tries < A.max_tries:
                    time.sleep(min(60, 2 ** tries)); continue
                self.send_response(502); self.send_header("Content-Length", "0"); self.end_headers(); return
            if r.status_code in (429, 500, 502, 503, 504) and tries < A.max_tries:
                r.read(); r.close(); time.sleep(min(60, 2 ** tries)); continue
            break
        self.send_response(r.status_code)
        for k, v in r.headers.items():
            if k.lower() not in ("content-length", "transfer-encoding", "connection", "content-encoding"):
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
        n = 0
        for chunk in r.iter_raw():
            if chunk:
                n += len(chunk)
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n"); self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n"); self.wfile.flush(); r.close()
        rec = {"t": time.strftime("%H:%M:%S"), "path": self.path, "status": r.status_code, "tries": tries,
               "req_bytes": len(body or b""), "resp_bytes": n, "s": round(time.time() - t0, 2), "client": self.client_address[0]}
        with LOCK:
            LOG.write(json.dumps(rec) + "\n"); LOG.flush()

    def do_POST(self):
        self._go("POST")

    def do_GET(self):
        self._go("GET")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", required=True); ap.add_argument("--key-file", required=True)
    ap.add_argument("--rpm", type=int, default=35); ap.add_argument("--max-tries", type=int, default=8)
    ap.add_argument("--port", type=int, default=8787); ap.add_argument("--log", default="logs/relay.jsonl")
    A = ap.parse_args(); A.key = Path(A.key_file).read_text().strip()
    LOG = open(A.log, "a"); CLIENT = httpx.Client(timeout=httpx.Timeout(600, connect=30))
    print(f"relay on :{A.port} -> {A.upstream} at {A.rpm} rpm", flush=True)
    ThreadingHTTPServer(("0.0.0.0", A.port), H).serve_forever()
