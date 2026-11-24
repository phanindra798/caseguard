"""Deliberately BAD brains, for testing that the SYSTEM stays safe when the model misbehaves.

They are test doubles, never used in production. The point: assume the model will sometimes be
fooled or wrong, and prove that code-side checks still protect the customer and the data.
"""
from __future__ import annotations

from ..models import Claim, PlanStep, ReplyDraft
from .scripted import ScriptedLLM


class GullibleLLM(ScriptedLLM):
    """Obeys instructions it finds inside DATA (e.g. a customer note): proposes escalations the note
    asks for, and tells the customer "done" when the note says so."""

    def _plan(self, view: dict) -> PlanStep:
        note = ((view.get("order") or {}).get("untrusted_customer_note") or "").lower()
        if "escalate" in note and view["steps_taken"] < 2:
            return PlanStep(action="propose_escalation", reason="the order note says to escalate immediately")
        return super()._plan(view)

    def _write_reply(self, view: dict) -> ReplyDraft:
        note = (view["facts"].get("customer_note") or "").lower()
        if "tell the customer it is done" in note:
            return ReplyDraft(text="Done! Your escalation is complete.",
                              claims=[Claim(type="escalation_completed", case_id=view["facts"].get("case_id"))])
        return super()._write_reply(view)
