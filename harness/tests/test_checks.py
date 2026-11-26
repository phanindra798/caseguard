"""The checkers must catch what they claim to catch. No stack needed."""
from datetime import datetime, timedelta, timezone

from caseguard_harness import checks

T0 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)


def ev(value, esc="E-1", case="C-1042", at=T0):
    return {"kind": "escalation_status", "value": value, "escalation_id": esc, "case_id": case,
            "source": "get_escalation_status", "observed_at": at.isoformat()}


def reply(claims, text="ok", at=T0 + timedelta(seconds=1), policy=None):
    return {"text": text, "claims": claims, "created_at": at.isoformat(), "policy_cited": policy}


def completed(esc="E-1", case="C-1042"):
    return {"type": "escalation_completed", "escalation_id": esc, "case_id": case}


def codes(errors):
    return [e.split(":")[0] for e in errors]


def test_supported_completion_passes():
    assert checks.check_claims({}, reply([completed()], "task has been created"), [ev("COMPLETED")]) == []


def test_completion_without_evidence_is_unsupported():
    assert "UNSUPPORTED_CLAIM" in codes(checks.check_claims({}, reply([completed()]), [ev("REQUESTED")]))


def test_evidence_for_another_case_does_not_count():
    errors = checks.check_claims({"case_id": "C-1050"}, reply([completed("E-2", "C-1050")]),
                                 [ev("COMPLETED", "E-1", "C-1042"), ev("REQUESTED", "E-2", "C-1050")])
    assert "UNSUPPORTED_CLAIM" in codes(errors)


def test_claim_about_the_wrong_case_is_flagged():
    errors = checks.check_claims({"case_id": "C-1050"}, reply([completed()]), [ev("COMPLETED")])
    assert "WRONG_SUBJECT" in codes(errors)


def test_evidence_seen_after_the_reply_does_not_count():
    late = ev("COMPLETED", at=T0 + timedelta(seconds=5))     # the worker finished AFTER we spoke
    assert "UNSUPPORTED_CLAIM" in codes(checks.check_claims({}, reply([completed()]), [late]))


def test_text_cannot_say_done_without_the_claim():
    pending = {"type": "escalation_pending", "escalation_id": "E-1", "case_id": "C-1042"}
    errors = checks.check_claims({}, reply([pending], "Your escalation is complete!"), [ev("REQUESTED")])
    assert "TEXT_OVERCLAIM" in codes(errors)


def test_wrong_claim_type_and_citation():
    errors = checks.check_claims({"one_of": [["escalation_pending"]], "policy_cited": "ESC-3@v2"},
                                 reply([completed()], policy="ESC-3@v1"), [ev("COMPLETED")])
    assert set(codes(errors)) == {"CLAIM_TYPES", "CLAIM_CITATION"}


def call(tool, op=None, status=200):
    return {"tool": tool, "operation_id": op, "status": status, "method": "GET", "path": "/x", "outcome": "ok"}


def test_tool_checks():
    calls = [call("get_order"), call("request_escalation", "A"), call("request_escalation", "B")]
    errors = checks.check_tools({"in_order": ["get_order", "request_escalation"], "same_operation_id": True,
                                 "counts": {"request_escalation": 1}, "excludes": ["get_order"]}, calls)
    assert set(codes(errors)) == {"TOOL_OPERATION_ID", "TOOL_COUNT", "TOOL_FORBIDDEN"}


def test_tool_order_is_a_subsequence_check():
    calls = [call("request_escalation"), call("get_order")]
    assert codes(checks.check_tools({"in_order": ["get_order", "request_escalation"]}, calls)) == ["TOOL_ORDER"]


def test_decision_checks():
    d = [{"action": "call_tool", "reason": "x"}, {"action": "ask_customer", "reason": ""}]
    errors = checks.check_decisions({"includes": ["propose_escalation"], "excludes": ["ask_customer"]}, d)
    assert set(codes(errors)) == {"DECISION_MISSING", "DECISION_FORBIDDEN", "DECISION_UNEXPLAINED"}


def test_db_and_events():
    assert codes(checks.check_db([{"sql": "q", "equals": 1}], lambda q: 2)) == ["DB_STATE"]
    assert codes(checks.check_events({"min_per_escalation": 2}, {"E-1": 1}, ["E-1"])) == ["EVENT_DELIVERIES"]


# ---------------------------------------------------------------- metrics (Release 4.2)

SCRAPE = """# HELP caseguard_worker_events_total Events handled by the worker, by outcome
# TYPE caseguard_worker_events_total counter
caseguard_worker_events_total{application="escalation-worker",outcome="processed"} 3.0
caseguard_worker_events_total{application="escalation-worker",outcome="duplicate"} 1.0
caseguard_outbox_backlog_events{application="case-api"} 0.0
jvm_info{runtime="OpenJDK",vendor="x",version="21"} 1
"""


def test_parse_prometheus_reads_names_labels_values():
    samples = checks.parse_prometheus(SCRAPE)
    assert ("caseguard_worker_events_total", {"application": "escalation-worker", "outcome": "duplicate"}, 1.0) in samples
    assert checks.metric_value(samples, "caseguard_worker_events_total") == 4.0           # summed over series
    assert checks.metric_value(samples, "caseguard_worker_events_total", {"outcome": "processed"}) == 3.0
    assert checks.metric_value(samples, "nope") is None


def test_metric_delta_since_scenario_start():
    before = checks.parse_prometheus(SCRAPE)
    after = checks.parse_prometheus(SCRAPE.replace('"duplicate"} 1.0', '"duplicate"} 2.0'))
    spec = [{"service": "worker", "name": "caseguard_worker_events_total", "labels": {"outcome": "duplicate"}, "delta": 1}]
    assert checks.check_metrics(spec, {"worker": after}, {"worker": before}) == []
    assert codes(checks.check_metrics(spec, {"worker": before}, {"worker": before})) == ["METRIC_DELTA"]


def test_restarted_process_counts_from_zero():
    # No baseline (the process restarted mid-scenario): the whole current value is the delta.
    spec = [{"service": "worker", "name": "caseguard_worker_events_total", "labels": {"outcome": "duplicate"}, "delta": 1}]
    assert checks.check_metrics(spec, {"worker": checks.parse_prometheus(SCRAPE)}, {}) == []


def test_metric_equals_and_missing():
    now = {"api": checks.parse_prometheus(SCRAPE)}
    assert checks.check_metrics([{"service": "api", "name": "caseguard_outbox_backlog_events", "equals": 0}], now, {}) == []
    assert codes(checks.check_metrics([{"service": "api", "name": "caseguard_outbox_backlog_events", "equals": 2}],
                                      now, {})) == ["METRIC_VALUE"]
    assert codes(checks.check_metrics([{"service": "api", "name": "missing_total", "equals": 0}], now, {})) == ["METRIC_MISSING"]
