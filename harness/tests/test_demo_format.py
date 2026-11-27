"""The demo's transcript helpers: short ids, collapsed repeats. No stack needed."""
from caseguard_harness.demo import _collapse, _short


def test_uuids_are_shortened_like_git():
    assert _short("GET /escalations/45504dde-494d-4290-84a4-f188c9ae59aa") == "GET /escalations/45504dde…"
    assert _short("GET /orders/1042") == "GET /orders/1042"


def test_repeats_collapse_in_order():
    assert _collapse(["a", "a", "a", "b", "a"]) == ["a ×3", "b", "a"]
    assert _collapse([]) == []
