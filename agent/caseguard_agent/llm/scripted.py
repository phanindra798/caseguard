"""A deterministic, rule-based stand-in for a language model.

Why it exists: tests and CI must be reproducible and free. This "brain" makes the same kind of
decisions a good model should make, so the graph, tools and harness can be tested exactly.
It is NOT the product's intelligence; the live model is. Release 3 measures the live model.

`overclaim=True` simulates a bad prompt that always tells the customer "it's done". The harness
must catch it (scenario S07).
"""
from __future__ import annotations

import re

from ..models import Claim, PlanStep, ReplyDraft

_ORDER_ID = re.compile(r"\b(\d{4})\b")
_WANTS_ESCALATION = re.compile(r"escalat|late|delay|not (?:arrived|here)|still waiting|hasn.t arrived", re.I)


class ScriptedLLM:
    def __init__(self, overclaim: bool = False):
        self.overclaim = overclaim

    # ------------------------------------------------------------------ planning
    def plan(self, view: dict) -> PlanStep:
        latest = view["latest_message"]
        named = _ORDER_ID.findall(latest)
        order, orders = view.get("order"), view.get("orders")
        wants_escalation = any(_WANTS_ESCALATION.search(m) for m in view["customer_messages"])

        # 1. The customer named an order we haven't loaded yet → load it.
        if named and (order is None or order["id"] != named[-1]) and view.get("not_found_order") != named[-1]:
            return PlanStep(action="call_tool", tool="get_order", args={"order_id": named[-1]},
                            reason=f"customer named order {named[-1]}")
        if view.get("not_found_order") and order is None:
            return PlanStep(action="answer", reason="that order is not on this customer's account")

        # 2. No order yet → find out which one they mean.
        if order is None:
            if orders is None:
                return PlanStep(action="call_tool", tool="list_orders", args={},
                                reason="customer didn't name an order; list their orders")
            late = [o for o in orders if o["late_business_days"] > 0 and o["status"] not in ("DELIVERED", "CANCELLED")]
            if len(late) == 1:
                return PlanStep(action="call_tool", tool="get_order", args={"order_id": late[0]["id"]},
                                reason=f"only one late order on the account: {late[0]['id']}")
            if len(late) > 1:
                options = ", ".join(f"#{o['id']} ({o['description']})" for o in late)
                return PlanStep(action="ask_customer",
                                question=f"I can see more than one delayed order: {options}. Which one do you mean?",
                                reason=f"{len(late)} late orders and the customer didn't say which")
            return PlanStep(action="answer", reason="no late orders on the account")

        # 3. Gather the remaining facts, one tool at a time.
        if view.get("read_error"):
            return PlanStep(action="answer", reason=f"could not read data ({view['read_error']} failed)")
        if view.get("case") is None and view.get("no_case"):
            return PlanStep(action="answer", reason=f"order {order['id']} has no support case")
        if view.get("case") is None:
            return PlanStep(action="call_tool", tool="get_cases", args={"order_id": order["id"]},
                            reason=f"need the support case for order {order['id']}")
        if view.get("policy") is None:
            return PlanStep(action="call_tool", tool="search_policy", args={"query": "late delivery escalation"},
                            reason="need the escalation policy that applies to this customer")

        # 4. Enough facts. Propose (code decides whether it's allowed).
        if wants_escalation:
            return PlanStep(action="propose_escalation",
                            reason="order, case and policy are known and the customer asked for an escalation")
        return PlanStep(action="answer", reason="customer asked for information, not an escalation")

    # ------------------------------------------------------------------ replying
    def write_reply(self, view: dict) -> ReplyDraft:
        allowed = [Claim(**c) for c in view["allowed_claims"]]
        f = view["facts"]
        if self.overclaim and f.get("escalation_id"):
            # The "bad prompt": reassure the customer no matter what the evidence says.
            return ReplyDraft(
                text=f"Good news: your escalation is complete and the team has been assigned (reference {f['escalation_id']}).",
                claims=[Claim(type="escalation_completed", escalation_id=f["escalation_id"], case_id=f.get("case_id"))],
                policy_cited=f.get("policy_ref"))

        claim = allowed[0] if allowed else None
        kind = claim.type if claim else "order_status"
        text = _TEMPLATES[kind](f)
        return ReplyDraft(text=text, claims=allowed, policy_cited=f.get("policy_ref") if kind in _CITES else None)


_CITES = {"escalation_completed", "escalation_pending", "escalation_declined", "approval_requested"}

_TEMPLATES = {
    "escalation_completed": lambda f: (
        f"I've escalated order {f['order_id']} to our tier-2 support team and a specialist task has been created "
        f"(reference {f['escalation_id']}). It is {f['days_late']} business days late, which qualifies under "
        f"policy {f['policy_ref']}."),
    "escalation_pending": lambda f: (
        f"I've submitted an escalation for order {f['order_id']} (reference {f['escalation_id']}). "
        "It's being processed now, and the support team will contact you once they pick it up."),
    "escalation_already_open": lambda f: (
        f"Order {f['order_id']} already has an open escalation (reference {f['escalation_id']}), "
        "so I haven't opened a second one."),
    "escalation_unconfirmed": lambda f: (
        f"I tried to escalate order {f['order_id']}, but I couldn't confirm whether it went through. "
        f"Your request reference is {f['operation_id']}; our team will check it, so there's no need to submit it again."),
    "escalation_not_submitted": lambda f: (
        f"I wasn't able to submit the escalation for order {f['order_id']} because our system is unavailable "
        "right now. Nothing was created; please try again in a few minutes."),
    "escalation_declined": lambda f: (
        f"Order {f['order_id']} is {f['days_late']} business day(s) late. Under policy {f['policy_ref']} an escalation "
        f"is possible after more than {f['policy_min_days']} business days, so I can't escalate it yet."),
    "approval_requested": lambda f: (
        f"Order {f['order_id']} qualifies for escalation, but because it's over $500, policy {f['policy_ref']} "
        f"requires a supervisor's approval first. I've sent the request (reference {f['approval_id']}) and "
        "I'll continue as soon as a supervisor decides."),
    "approval_rejected": lambda f: (
        f"A supervisor reviewed the escalation for order {f['order_id']} and decided not to approve it, "
        "so I haven't escalated it. Our support team can tell you more about the next steps."),
    "case_not_open": lambda f: f"The support case for order {f['order_id']} is closed, so it can't be escalated.",
    "needs_more_info": lambda f: f["question"],
    "refused": lambda f: f.get("refusal") or "Sorry, I can't help with that request.",
    "order_status": lambda f: (
        f"Order {f['order_id']} ({f['order_description']}) is {f['order_status']}; it was promised for {f['promised_date']}."
        if f.get("order_id") else "I couldn't find a delayed order on your account."),
}
