"""Run one conversation turn and report everything that happened during it.

The result is what the harness (Phase 1.5) inspects: the reply and its claims, the planner's
decisions, every Case API call, and the evidence collected.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Session:
    """A TRUSTED identity, e.g. resolved from a login token. Never derived from chat text."""
    tenant_id: str
    customer_id: str
    region: str


class AgentRunner:
    def __init__(self, graph):
        self.graph = graph

    def turn(self, conversation_id: str, session: Session, message: str) -> dict:
        config = {"configurable": {"thread_id": conversation_id}}
        before = self.graph.get_state(config).values or {}
        seen = {k: len(before.get(k, [])) for k in ("decisions", "tool_calls", "evidence")}

        final = self.graph.invoke(
            {"tenant_id": session.tenant_id, "customer_id": session.customer_id, "region": session.region,
             "messages": [{"role": "user", "content": message}]},
            config)

        return {
            "reply": final["reply"],
            "decisions": final.get("decisions", [])[seen["decisions"]:],
            "tool_calls": final.get("tool_calls", [])[seen["tool_calls"]:],
            "evidence": final.get("evidence", [])[seen["evidence"]:],
            "all_evidence": final.get("evidence", []),
            "operations": final.get("operations", {}),
        }
