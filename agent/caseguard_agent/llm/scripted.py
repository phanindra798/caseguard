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
from ..replies import render_reply

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

        # 2. No order loaded this turn, but the conversation is about one → re-read it.
        if order is None and not named and view.get("focus_order_id"):
            return PlanStep(action="call_tool", tool="get_order", args={"order_id": view["focus_order_id"]},
                            reason=f"re-checking order {view['focus_order_id']} in the system; facts may have changed")

        # 3. No order yet → find out which one they mean.
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

        # 4. Gather the remaining facts, one tool at a time.
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

        # 5. Enough facts. Propose (code decides whether it's allowed).
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

        return render_reply(allowed, f)
