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
