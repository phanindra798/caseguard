"""A minimal OTLP/HTTP trace receiver, so the harness can ASSERT on traces (not just look at them).

All three services (agent, Case API, worker) export spans to http://localhost:4318/v1/traces; this
collects them in memory. In Docker Compose and CI, Jaeger plays this role for humans.
"""
from __future__ import annotations

import gzip
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


class TraceCollector:
    def __init__(self, port: int = 4318):
        self.port = port
        self.spans: list[dict] = []
        self.requests: list[dict] = []          # what arrived, for debugging export problems
        self._lock = threading.Lock()
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _body(self) -> bytes:
                if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
                    # Larger batches arrive chunked (no Content-Length): read chunk by chunk.
                    data = b""
                    while True:
                        size = int(self.rfile.readline().strip().split(b";")[0], 16)
                        if size == 0:
                            self.rfile.readline()
                            return data
                        data += self.rfile.read(size)
                        self.rfile.readline()
                return self.rfile.read(int(self.headers.get("Content-Length") or 0))

            def do_POST(self):
                note = {"path": self.path, "agent": self.headers.get("User-Agent", ""),
                        "type": self.headers.get("Content-Type", ""), "encoding": self.headers.get("Content-Encoding", ""),
                        "chunked": self.headers.get("Transfer-Encoding", "")}
                try:
                    body = self._body()
                    note["bytes"] = len(body)
                    if self.headers.get("Content-Encoding", "").lower() == "gzip":
                        body = gzip.decompress(body)
                    if self.path.endswith("/v1/traces"):
                        note["spans"] = collector._ingest(body)
                except Exception as e:            # never crash the exporter; record why
                    note["error"] = f"{type(e).__name__}: {e}"
                with collector._lock:
                    collector.requests.append(note)
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self.send_header("Content-Length", "0")
                self.end_headers()

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self._server.shutdown()

    def clear(self) -> None:
        with self._lock:
            self.spans.clear()

    def _ingest(self, body: bytes) -> None:
        req = ExportTraceServiceRequest()
        req.ParseFromString(body)
        rows = []
        for rs in req.resource_spans:
            service = next((a.value.string_value for a in rs.resource.attributes if a.key == "service.name"), "?")
            for ss in rs.scope_spans:
                for s in ss.spans:
                    rows.append({"trace_id": s.trace_id.hex(), "span_id": s.span_id.hex(),
                                 "parent_id": s.parent_span_id.hex(), "name": s.name, "service": service})
        with self._lock:
            self.spans.extend(rows)
        return len(rows)

    def traces(self) -> dict[str, list[dict]]:
        by_trace: dict[str, list[dict]] = defaultdict(list)
        with self._lock:
            for s in self.spans:
                by_trace[s["trace_id"]].append(s)
        return dict(by_trace)
