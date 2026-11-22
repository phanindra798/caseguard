"""Runtime output guard: the last check before a reply reaches the customer.

The model may only make the claims code has proven (`allowed`). If its reply claims anything else,
claims it about another escalation or case, or its WORDS say "done" without that being proven, the
reply is replaced by a verified template reply. The harness checks the same things independently,
but this guard protects customers in production, not just in tests.
"""
from __future__ import annotations

from .models import Claim, ReplyDraft

_DONE_PHRASES = ("escalation is complete", "is complete", "has been created", "has been assigned",
                 "i've escalated", "i have escalated", "escalated it", "it is done", "it's done",
                 "all done", "successfully escalated", "has been approved")


def violations(draft: ReplyDraft, allowed: list[Claim]) -> list[str]:
    found = []
    allowed_keys = {(c.type, c.escalation_id, c.case_id) for c in allowed}
    for c in draft.claims:
        if (c.type, c.escalation_id, c.case_id) not in allowed_keys:
            found.append(f"claim '{c.type}' (escalation {c.escalation_id}, case {c.case_id}) is not proven")
    if not any(c.type == "escalation_completed" for c in allowed):
        said = [p for p in _DONE_PHRASES if p in draft.text.lower()]
        if said:
            found.append(f"text says {said} but completion is not proven")
    return found
