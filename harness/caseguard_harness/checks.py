"""Checkers. Each returns a list of failure strings, prefixed with a stable code:

  DECISION_*        what the planner chose
  TOOL_*            what the agent did against the API (from OUR tool-call trace)
  CLAIM_*           what the reply asserted
  UNSUPPORTED_CLAIM a status claim with no matching evidence before the reply
  WRONG_SUBJECT     a claim about a different case than the one discussed
  TEXT_OVERCLAIM    the words say "done" but the structured claims don't
  DB_*, EVENT_*, LOG_*  the real system's state after the conversation

Evidence comes from the agent's tool layer (real API responses), never from the LLM.
"""
from __future__ import annotations

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
