"""An HTTP-aware fault proxy between the agent and the real Case API.

Why not Toxiproxy? Toxiproxy works on TCP connections: it can slow or cut a whole connection,
but it can't say "let the POST /escalations reach the server, then lose ITS response, and leave
GET requests alone". These scenarios need exactly that, so the proxy understands HTTP.

Fault modes (each applies to the next `times` requests matching method + path regex):
  lose_response  forward to the real server (it ACTS), then hold the response until the client
                 has timed out: the "did it happen or not?" case
  unavailable    answer 503 WITHOUT forwarding: the server never saw the request
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx


@dataclass
class Fault:
    method: str
    path: str               # regex
    mode: str               # "lose_response" | "unavailable"
    times: int
    hold_s: float = 2.5     # must be longer than the agent's client timeout

    def matches(self, method: str, path: str) -> bool:
        return self.times > 0 and method == self.method and re.search(self.path, path) is not None


class FaultProxy:
    def __init__(self, upstream: str, port: int = 8095):
        self.upstream, self.port = upstream, port
        self.faults: list[Fault] = []
        self.injected: list[dict] = []          # what the proxy actually did, for the report
        self._lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):        # keep test output quiet
                pass

            def _handle(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fault = proxy._take(self.command, self.path)
                if fault and fault.mode == "unavailable":
                    proxy._note(fault, self.command, self.path)
                    self._send(503, b'{"code":"unavailable"}', {"Content-Type": "application/json"})
                    return
                headers = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "content-length")}
                try:
                    resp = httpx.request(self.command, proxy.upstream + self.path, headers=headers,
                                         content=body, timeout=30)
                except httpx.HTTPError:
                    # Upstream down (e.g. a scenario crashed the Case API): answer like a real gateway.
                    self._send(502, b'{"code":"bad_gateway"}', {"Content-Type": "application/json"})
                    return
                if fault and fault.mode == "lose_response":
                    proxy._note(fault, self.command, self.path, upstream_status=resp.status_code)
                    time.sleep(fault.hold_s)      # the client gives up before this ends
                    return                        # close without answering
                self._send(resp.status_code, resp.content,
                           {k: v for k, v in resp.headers.items()
                            if k.lower() in ("content-type", "location", "idempotent-replayed")})

            def _send(self, status, content, headers):
                try:
                    self.send_response(status)
                    for k, v in headers.items():
                        self.send_header(k, v)
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = _handle

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "FaultProxy":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()

    def add(self, fault: Fault) -> None:
        with self._lock:
            self.faults.append(fault)

    def clear(self) -> None:
        with self._lock:
            self.faults.clear()
            self.injected.clear()

    def _take(self, method: str, path: str) -> Fault | None:
        with self._lock:
            for f in self.faults:
                if f.matches(method, path.split("?")[0]):
                    f.times -= 1
                    return f
        return None

    def _note(self, fault: Fault, method: str, path: str, upstream_status: int | None = None):
        with self._lock:
            self.injected.append({"mode": fault.mode, "method": method, "path": path,
                                  "upstream_status": upstream_status})
