"""Run one conversation turn (or resume a paused one) and report everything that happened.

The result is what the harness inspects: the reply and its claims, the planner's decisions, every
Case API call, and the evidence collected.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from langgraph.types import Command

from .llm.prompts import PROMPT_VERSION
from .models import utcnow
from .tracing import current_traceparent, tracer


@dataclass(frozen=True)
class Session:
    """A TRUSTED identity, e.g. resolved from a login token. Never derived from chat text."""
    tenant_id: str
    customer_id: str
    region: str


class AgentRunner:
    def __init__(self, graph):
        self.graph = graph

    @staticmethod
    def _config(conversation_id: str) -> dict:
        return {"configurable": {"thread_id": conversation_id}}

    def waiting_for_approval(self, conversation_id: str) -> str | None:
        """The approval id this conversation is paused on, or None."""
        snap = self.graph.get_state(self._config(conversation_id))
        if snap.next and "wait_for_approval" in snap.next:
            return str((snap.values.get("approval") or {}).get("id"))
        return None

    def turn(self, conversation_id: str, session: Session, message: str) -> dict:
        waiting = self.waiting_for_approval(conversation_id)
        if waiting:
            # Don't restart the graph: that would abandon the paused approval flow. Answer from state.
            values = self.graph.get_state(self._config(conversation_id)).values
            case_id = (values.get("case") or {}).get("id")
            text = (f"Your escalation is still waiting for a supervisor's approval (reference {waiting}). "
                    "I'll continue as soon as they decide.")
            claims = [{"type": "approval_requested", "escalation_id": None, "case_id": case_id}]
            return {"reply": {"text": text, "claims": claims, "policy_cited": None, "allowed_claims": claims,
                              "created_at": utcnow().isoformat()},
                    "decisions": [], "tool_calls": [], "evidence": [],
                    "all_evidence": values.get("evidence", []), "operations": values.get("operations", {}),
                    "waiting_for_approval": waiting}
        return self._run(conversation_id, {
            "tenant_id": session.tenant_id, "customer_id": session.customer_id, "region": session.region,
            "messages": [{"role": "user", "content": message}]})

    def resume(self, conversation_id: str, signal: dict) -> dict:
        """Continue a paused conversation (e.g. after a supervisor decided)."""
        if not self.waiting_for_approval(conversation_id):
            raise ValueError(f"conversation {conversation_id} is not waiting for an approval")
        # Sent as a JSON STRING on purpose: Command(resume=<dict>) is read as a map of interrupt ids to
        # values, so a plain dict (e.g. an empty {}) would silently resume nothing and pause again.
        return self._run(conversation_id, Command(resume=json.dumps(signal)))

    def _run(self, conversation_id: str, graph_input) -> dict:
        config = self._config(conversation_id)
        before = self.graph.get_state(config).values or {}
        seen = {k: len(before.get(k, [])) for k in ("decisions", "tool_calls", "evidence")}
        meter = getattr(getattr(self.graph, "_caseguard_llm", None), "meter", None)
        usage_before = meter.snapshot() if meter else None
        with tracer.start_as_current_span("agent.turn") as span:
            span.set_attribute("caseguard.conversation_id", conversation_id)
            span.set_attribute("caseguard.prompt_version", PROMPT_VERSION)
            span.set_attribute("caseguard.resume", not isinstance(graph_input, dict))
            # Hand the turn's trace context to every node explicitly (see tracing.traced_node).
            run_config = {"configurable": {**config["configurable"], "traceparent": current_traceparent()}}
            final = self.graph.invoke(graph_input, run_config)
            span.set_attribute("caseguard.claims", [c["type"] for c in final["reply"]["claims"]])
            usage = meter.delta(usage_before, meter.snapshot()) if meter else None
            if usage:
                span.set_attribute("caseguard.llm.calls", usage.calls)
                span.set_attribute("caseguard.llm.input_tokens", usage.input_tokens)
                span.set_attribute("caseguard.llm.output_tokens", usage.output_tokens)
                span.set_attribute("caseguard.llm.tokens_estimated", usage.estimated)
            span.set_attribute("caseguard.trace_hint", "agent -> case-api -> kafka -> worker")
        return {
            "reply": final["reply"],
            "decisions": final.get("decisions", [])[seen["decisions"]:],
            "tool_calls": final.get("tool_calls", [])[seen["tool_calls"]:],
            "evidence": final.get("evidence", [])[seen["evidence"]:],
            "all_evidence": final.get("evidence", []),
            "operations": final.get("operations", {}),
            "waiting_for_approval": self.waiting_for_approval(conversation_id),
            "usage": None if usage is None else {"llm_calls": usage.calls, "input_tokens": usage.input_tokens,
                                                 "output_tokens": usage.output_tokens, "estimated": usage.estimated,
                                                 "cost_usd": usage.cost_usd()},
        }

    def messages(self, conversation_id: str) -> list[dict]:
        return self.graph.get_state(self._config(conversation_id)).values.get("messages", [])
