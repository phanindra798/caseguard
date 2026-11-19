"""Data shapes shared by the graph, the LLM layer and the harness.

The important idea: EVIDENCE is created by our code from real API responses, never by the LLM.
CLAIMS are what the reply asserts. The harness checks every claim against evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

ClaimType = Literal[
    "escalation_completed",     # the follow-up task was created (status COMPLETED)
    "escalation_pending",       # escalation accepted, worker hasn't finished (status REQUESTED)
    "escalation_already_open",  # someone/something escalated this case before
    "escalation_unconfirmed",   # we could not find out whether it went through
    "escalation_not_submitted", # confirmed: nothing was created
    "escalation_declined",      # policy says not eligible
    "approval_requested",       # eligible, sent to a supervisor; waiting for the decision
    "approval_rejected",        # a supervisor said no
    "case_not_open",            # the case is closed
    "needs_more_info",          # we asked the customer a question
    "refused",                  # blocked by a guardrail, or not the customer's data
    "order_status",             # plain information answer
]

READ_TOOLS = ("list_orders", "get_order", "get_cases", "search_policy")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Evidence(BaseModel):
    """A receipt: what the system actually reported, about what, and when we saw it."""
    kind: str                       # e.g. "escalation_status"
    value: str                      # e.g. "COMPLETED"
    escalation_id: str | None = None
    case_id: str | None = None
    source: str                     # which tool call produced it
    observed_at: datetime = Field(default_factory=utcnow)


class Claim(BaseModel):
    type: ClaimType
    escalation_id: str | None = None
    case_id: str | None = None


class PlanStep(BaseModel):
    """One planner decision. `reason` is logged and tested, so decisions are explainable."""
    action: Literal["call_tool", "ask_customer", "propose_escalation", "answer"]
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    question: str | None = None     # for ask_customer
    reason: str


class ReplyDraft(BaseModel):
    """What the LLM writes. Claims are what it SAYS; the harness checks them against evidence."""
    text: str
    claims: list[Claim]
    policy_cited: str | None = None


class ToolCall(BaseModel):
    """Trace of one call to the Case API (written by our client, not the LLM)."""
    tool: str
    method: str
    path: str
    status: int | None              # HTTP status, or None for timeout / connection error
    outcome: str                    # "ok" | error code | "timeout" | "unavailable"
    operation_id: str | None = None
    at: datetime = Field(default_factory=utcnow)


def dump(model: BaseModel) -> dict:
    """State stores plain JSON-friendly dicts so checkpoints serialize cleanly."""
    return model.model_dump(mode="json")
