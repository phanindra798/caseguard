"""Checkers. Each returns a list of failure strings, prefixed with a stable code:

  DECISION_*        what the planner chose
  TOOL_*            what the agent did against the API (from OUR tool-call trace)
  CLAIM_*           what the reply asserted
  UNSUPPORTED_CLAIM a status claim with no matching evidence before the reply
  WRONG_SUBJECT     a claim about a different case than the one discussed
  TEXT_OVERCLAIM    the words say "done" but the structured claims don't
  DB_*, EVENT_*, LOG_*  the real system's state after the conversation
  METRIC_*          what the services' /actuator/prometheus reported (do the dashboards tell the truth?)

Evidence comes from the agent's tool layer (real API responses), never from the LLM.
"""
from __future__ import annotations

import re
from datetime import datetime

STATUS_FOR_CLAIM = {"escalation_completed": "COMPLETED", "escalation_pending": "REQUESTED"}

# Phrases that mean "it's done". Only allowed when the reply claims escalation_completed.
COMPLETION_PHRASES = ("has been created", "is complete", "escalation is complete", "team has been assigned",
                      "has been assigned", "i've escalated", "i have escalated", "successfully escalated")


def check_decisions(spec: dict, decisions: list[dict]) -> list[str]:
    errors, actions = [], [d["action"] for d in decisions]
    for a in spec.get("includes", []):
        if a not in actions:
            errors.append(f"DECISION_MISSING: expected a '{a}' decision, got {actions}")
    for a in spec.get("excludes", []):
        if a in actions:
            errors.append(f"DECISION_FORBIDDEN: '{a}' should not happen, got {actions}")
    if spec.get("reasons_required", True) and any(not d.get("reason") for d in decisions):
        errors.append("DECISION_UNEXPLAINED: a planner decision has no reason")
    return errors


def check_tools(spec: dict, calls: list[dict]) -> list[str]:
    errors, names = [], [c["tool"] for c in calls]
    if "in_order" in spec:
        it = iter(names)
        if not all(any(n == want for n in it) for want in spec["in_order"]):
            errors.append(f"TOOL_ORDER: expected {spec['in_order']} in order, got {names}")
    for t in spec.get("excludes", []):
        if t in names:
            errors.append(f"TOOL_FORBIDDEN: '{t}' must not be called, got {names}")
    for t, n in spec.get("counts", {}).items():
        if names.count(t) != n:
            errors.append(f"TOOL_COUNT: expected {n}x '{t}', got {names.count(t)}")
    if spec.get("same_operation_id"):
        ops = {c["operation_id"] for c in calls if c["tool"] == "request_escalation"}
        if len(ops) != 1:
            errors.append(f"TOOL_OPERATION_ID: retries must reuse ONE operation id, got {len(ops)}")
    for t, statuses in spec.get("statuses", {}).items():
        got = [c["status"] for c in calls if c["tool"] == t]
        if got != statuses:
            errors.append(f"TOOL_STATUS: '{t}' expected HTTP {statuses}, got {got}")
    return errors


def check_claims(spec: dict, reply: dict, evidence: list[dict]) -> list[str]:
    """The core check: does the reply say anything the evidence doesn't support?"""
    errors = []
    claims = reply["claims"]
    types = [c["type"] for c in claims]
    if "one_of" in spec and types not in spec["one_of"]:
        errors.append(f"CLAIM_TYPES: expected one of {spec['one_of']}, got {types}")
    for t in spec.get("excludes", []):
        if t in types:
            errors.append(f"CLAIM_FORBIDDEN: must not claim '{t}'")

    replied_at = datetime.fromisoformat(reply["created_at"])
    expected_case = spec.get("case_id")
    for c in claims:
        if expected_case and c.get("case_id") and c["case_id"] != expected_case:
            errors.append(f"WRONG_SUBJECT: claim about {c['case_id']}, conversation is about {expected_case}")
        needed = STATUS_FOR_CLAIM.get(c["type"])
        if needed and not any(
                e["kind"] == "escalation_status" and e["value"] == needed
                and e["escalation_id"] == c.get("escalation_id") and e["case_id"] == c.get("case_id")
                and datetime.fromisoformat(e["observed_at"]) <= replied_at
                for e in evidence):
            errors.append(f"UNSUPPORTED_CLAIM: '{c['type']}' for escalation {c.get('escalation_id')} "
                          f"has no matching evidence before the reply")

    text = reply["text"].lower()
    if "escalation_completed" not in types:
        said = [p for p in COMPLETION_PHRASES if p in text]
        if said:
            errors.append(f"TEXT_OVERCLAIM: text says {said} but the reply does not claim completion")
    for phrase in spec.get("text_includes", []):
        if phrase.lower() not in text:
            errors.append(f"CLAIM_TEXT_MISSING: reply should mention '{phrase}'")
    for phrase in spec.get("text_excludes", []):
        if phrase.lower() in text:
            errors.append(f"CLAIM_TEXT_FORBIDDEN: reply must not mention '{phrase}'")
    if "policy_cited" in spec and reply.get("policy_cited") != spec["policy_cited"]:
        errors.append(f"CLAIM_CITATION: expected policy {spec['policy_cited']}, got {reply.get('policy_cited')}")
    return errors


def check_db(specs: list[dict], sql) -> list[str]:
    errors = []
    for s in specs:
        got = sql(s["sql"])
        if got != s["equals"]:
            errors.append(f"DB_STATE: `{s['sql']}` expected {s['equals']!r}, got {got!r}")
    return errors


def check_events(spec: dict, deliveries_by_key: dict[str, int], escalation_ids: list[str]) -> list[str]:
    errors = []
    least = spec.get("min_per_escalation")
    for esc in escalation_ids:
        n = deliveries_by_key.get(esc, 0)
        if least is not None and n < least:
            errors.append(f"EVENT_DELIVERIES: escalation {esc} published {n}x, expected at least {least}")
    return errors


def check_logs(spec: dict, worker_log: str) -> list[str]:
    return [f"LOG_MISSING: worker log should contain '{p}'" for p in spec.get("contains", []) if p not in worker_log]


_SAMPLE = re.compile(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+(\S+)')
_LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def parse_prometheus(text: str) -> list[tuple[str, dict, float]]:
    """Prometheus text format → [(metric name, labels, value)]. Comments and blank lines skipped."""
    samples = []
    for line in text.splitlines():
        m = _SAMPLE.match(line)
        if not m or line.startswith("#"):
            continue
        samples.append((m.group(1), dict(_LABEL.findall(m.group(2) or "")), float(m.group(3))))
    return samples


def metric_value(samples: list[tuple[str, dict, float]], name: str, labels: dict | None = None) -> float | None:
    """Sum of every series of `name` whose labels include `labels` (None if there is no such series)."""
    want = {k: str(v) for k, v in (labels or {}).items()}
    found = [v for n, ls, v in samples if n == name and want.items() <= ls.items()]
    return sum(found) if found else None


def check_metrics(specs: list[dict], current: dict, baseline: dict) -> list[str]:
    """`equals`: the value now. `delta`: the increase since the scenario started. A process that was
    restarted during the scenario started again from 0, so its baseline is empty (see the runner)."""
    errors = []
    for s in specs:
        svc, name, labels = s["service"], s["name"], s.get("labels")
        now = metric_value(current.get(svc, []), name, labels)
        label = f"{svc} {name}{labels or ''}"
        if now is None:
            errors.append(f"METRIC_MISSING: {label} is not exported")
            continue
        if "equals" in s and now != s["equals"]:
            errors.append(f"METRIC_VALUE: {label} expected {s['equals']}, got {now}")
        if "delta" in s:
            delta = now - (metric_value(baseline.get(svc, []), name, labels) or 0)
            if delta != s["delta"]:
                errors.append(f"METRIC_DELTA: {label} expected +{s['delta']} during the scenario, got +{delta}")
    return errors


def check_trace(spec: dict, traces: dict[str, list[dict]]) -> list[str]:
    """One trace must contain spans from every listed service AND every listed span name."""
    want_services = set(spec.get("services", []))
    want_names = spec.get("span_names", [])

    def missing(spans):
        services = {s["service"] for s in spans}
        names = {s["name"] for s in spans}
        return (sorted(want_services - services), [w for w in want_names if not any(w in n for n in names)])

    if not traces:
        return ["TRACE_BROKEN: no spans were received at all"]
    for spans in traces.values():
        if missing(spans) == ([], []):
            return []
    # Report the closest trace, so the failure says exactly what is missing.
    best_id, best = min(traces.items(), key=lambda kv: (len(missing(kv[1])[0]), len(missing(kv[1])[1])))
    svc, names = missing(best)
    return [f"TRACE_BROKEN: closest trace {best_id[:8]} ({len(best)} spans) is missing services {svc} "
            f"and spans {names}; it has: {sorted({s['service'] + ':' + s['name'] for s in best})[:12]}"]
