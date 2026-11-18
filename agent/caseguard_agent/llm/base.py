"""The two jobs the graph gives to a language model."""
from __future__ import annotations

from typing import Protocol

from ..models import PlanStep, ReplyDraft


class AgentLLM(Protocol):
    def plan(self, view: dict) -> PlanStep:
        """Decide the next step: which READ tool to call, ask the customer, propose an escalation,
        or answer. `view` holds facts our code has gathered (never raw credentials or identity)."""
        ...

    def write_reply(self, view: dict) -> ReplyDraft:
        """Phrase the reply. `view["allowed_claims"]` lists what code has PROVEN may be claimed.
        The model is told to stay within it; the harness checks that it did."""
        ...
