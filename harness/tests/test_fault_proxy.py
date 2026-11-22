"""The fault proxy really forwards, really loses responses, and really refuses without forwarding."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from caseguard_harness.fault_proxy import Fault, FaultProxy


@pytest.fixture
def upstream():
    hits = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            hits.append(self.path)
            self.send_response(202)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_GET = do_POST

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", hits
    srv.shutdown()


@pytest.fixture
def proxy(upstream):
    p = FaultProxy(upstream[0], port=0).start()
    p.port = p._server.server_port
    yield p
    p.stop()


def test_passes_through(proxy, upstream):
    assert httpx.post(proxy.url + "/cases/C-1/escalations").status_code == 202
    assert upstream[1] == ["/cases/C-1/escalations"]


def test_lose_response_still_reaches_the_server(proxy, upstream):
    proxy.add(Fault("POST", r"/escalations$", "lose_response", times=1, hold_s=1.0))
    with pytest.raises(httpx.TimeoutException):
        httpx.post(proxy.url + "/cases/C-1/escalations", timeout=0.3)
    assert upstream[1] == ["/cases/C-1/escalations"], "the server acted, only the answer was lost"
    assert httpx.post(proxy.url + "/cases/C-1/escalations").status_code == 202   # only `times` requests


def test_unavailable_never_reaches_the_server(proxy, upstream):
    proxy.add(Fault("POST", r"/escalations$", "unavailable", times=1))
    assert httpx.post(proxy.url + "/cases/C-1/escalations").status_code == 503
    assert upstream[1] == []


def test_faults_only_match_their_method_and_path(proxy, upstream):
    proxy.add(Fault("POST", r"/escalations$", "unavailable", times=5))
    assert httpx.get(proxy.url + "/orders/1042").status_code == 202


def test_upstream_down_is_a_502_not_a_crash():
    p = FaultProxy("http://127.0.0.1:1", port=0).start()   # nothing listens on port 1
    p.port = p._server.server_port
    try:
        assert httpx.get(p.url + "/orders").status_code == 502
    finally:
        p.stop()


def test_trace_collector_reads_chunked_and_gzip_otlp():
    """Regression: Java exporters send larger batches chunked; the collector once dropped them."""
    import gzip as _gzip
    import http.client
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from caseguard_harness.trace_collector import TraceCollector

    req = ExportTraceServiceRequest()
    rs = req.resource_spans.add()
    attr = rs.resource.attributes.add()
    attr.key, attr.value.string_value = "service.name", "escalation-worker"
    span = rs.scope_spans.add().spans.add()
    span.trace_id, span.span_id, span.name = bytes(16 * [7]), bytes(8 * [1]), "process"
    payload = _gzip.compress(req.SerializeToString())

    col = TraceCollector(port=0)
    port = col._server.server_port
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.putrequest("POST", "/v1/traces")
        conn.putheader("Transfer-Encoding", "chunked")
        conn.putheader("Content-Encoding", "gzip")
        conn.endheaders()
        for i in range(0, len(payload), 7):                      # many small chunks
            part = payload[i:i + 7]
            conn.send(f"{len(part):x}\r\n".encode() + part + b"\r\n")
        conn.send(b"0\r\n\r\n")
        assert conn.getresponse().status == 200
        assert [(s["service"], s["name"]) for s in col.spans] == [("escalation-worker", "process")]
    finally:
        col.stop()


def test_proxy_passes_the_callers_trace_header_through_unchanged(proxy, upstream):
    """Regression: the proxy once replaced the agent's traceparent with its own, breaking traces."""
    import http.server, threading as th
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen["traceparent"] = self.headers.get("traceparent")
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    th.Thread(target=srv.serve_forever, daemon=True).start()
    p = FaultProxy(f"http://127.0.0.1:{srv.server_port}", port=0).start()
    p.port = p._server.server_port
    tp = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    try:
        assert httpx.get(p.url + "/orders", headers={"traceparent": tp}).status_code == 200
        assert seen["traceparent"] == tp
    finally:
        p.stop()
        srv.shutdown()
