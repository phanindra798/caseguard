"""Verified reply texts: one template per claim type, filled ONLY with facts our code gathered.

Used by the scripted brain, and by the output guard to replace a model reply that claims more than
the evidence supports.
"""
from __future__ import annotations

from .models import Claim, ReplyDraft


def render_reply(allowed: list[Claim], facts: dict) -> ReplyDraft:
    kind = allowed[0].type if allowed else "order_status"
    return ReplyDraft(text=_TEMPLATES[kind](facts), claims=allowed,
                      policy_cited=facts.get("policy_ref") if kind in _CITES else None)


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
    "no_applicable_policy": lambda f: (
        f"I couldn't find the escalation policy that applies to order {f['order_id']}, so I can't decide on an "
        "escalation automatically."),
    "case_not_open": lambda f: f"The support case for order {f['order_id']} is closed, so it can't be escalated.",
    "needs_more_info": lambda f: f["question"],
    "refused": lambda f: f.get("refusal") or "Sorry, I can't help with that request.",
    "order_status": lambda f: (
        f"Order {f['order_id']} ({f['order_description']}) is {f['order_status']}; it was promised for {f['promised_date']}."
        if f.get("order_id") else "I couldn't find a delayed order on your account."),
}
