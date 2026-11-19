"""The agent as a LangGraph state machine.

    START → guardrail ─(blocked)──────────────────────────────────────────► compose_reply
               │ok
               ▼
            plan ◄──────────── run_read_tool        (LLM chooses; READ tools only; step budget)
               │ ask_customer / answer ─────────────────────────────────► compose_reply
               │ propose_escalation
               ▼
          check_eligibility (CODE) ─(not eligible / approval / closed)──► compose_reply
               │ eligible              └─(missing facts)──► plan
               ▼
          prepare_action   operation id per action fingerprint, checkpointed BEFORE the write
               ▼
            execute ─ accepted / already_open ──► verify ──────────────► compose_reply
               └─ unknown (timeouts) ──► reconcile ─ found ──► verify
                                                   ├ confirmed not created ──► compose_reply
                                                   └ still unknown ──────────► compose_reply

Split of responsibilities: the LLM decides WHAT TO FIND OUT and WHAT TO PROPOSE.
Code decides WHAT IS ALLOWED and WHAT IS TRUE. The harness checks both.
"""
from __future__ import annotations

import operator
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from .business_days import business_days_late
from .case_api import (AlreadyOpen, CaseApiClient, CaseApiError, CaseNotOpen, NotFound,
                       OperationConflict, Unavailable)
from .llm.base import AgentLLM
from .models import READ_TOOLS, Claim, Evidence, PlanStep, dump, utcnow
from .policies import Policy, search_policy

IDENTITY_KEYS = {"tenant_id", "customer_id", "tenantId", "customerId", "region"}
_INJECTION = re.compile(
    r"ignore (all |any |the )?(previous|prior|above) (instructions|rules)|system prompt|you are now|developer mode",
    re.I)


# ======================================================================================= state
class AgentState(TypedDict, total=False):
    # Identity: from the trusted session on EVERY turn. The LLM never writes these.
    tenant_id: str
    customer_id: str
    region: str
    # Conversation-long logs (LangGraph appends with operator.add).
    messages: Annotated[list[dict], operator.add]
    decisions: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[dict], operator.add]
    evidence: Annotated[list[dict], operator.add]
    # Conversation-long facts.
    orders: list[dict] | None
    order: dict | None
    case: dict | None
    policy: dict | None
    operations: dict            # {fingerprint: {"id": uuid, "status": ...}}
    # Per-turn working fields (reset by the guardrail node at the start of every turn).
    steps: int
    next: dict | None
    blocked: bool
    not_found_order: str | None
    no_case: bool
    read_error: str | None
    eligibility: str | None
    days_late: int | None
    decision_reason: str | None
    current_op: str | None
    execute_outcome: str | None
    escalation: dict | None
    reply: dict | None


@dataclass
class AgentDeps:
    """Everything the graph needs from the outside world, injectable for tests."""
    llm: AgentLLM
    case_api: Callable[[str, str], CaseApiClient]     # (tenant_id, customer_id) -> client
    policies: list[Policy]
    today: Callable[[], date] = date.today
    retry_delays: tuple[float, ...] = (0.5, 1.0)      # 3 attempts in total
    poll_delays: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)
    max_steps: int = 6
    sleep: Callable[[float], None] = field(default=time.sleep)


# ======================================================================================= helpers
def _latest_user(state: AgentState) -> str:
    return next((m["content"] for m in reversed(state.get("messages", [])) if m["role"] == "user"), "")


def _order_summary(o: dict, today: date) -> dict:
    return {"id": o["id"], "description": o["description"], "status": o["status"],
            "amount_cents": o["amountCents"], "promised_date": o["promisedDate"],
            "late_business_days": business_days_late(date.fromisoformat(o["promisedDate"]), today)}


def _escalation_evidence(dto: dict, source: str) -> dict:
    return dump(Evidence(kind="escalation_status", value=dto["status"], escalation_id=str(dto["id"]),
                         case_id=dto["caseId"], source=source))


def allowed_claims(state: AgentState) -> list[dict]:
    """What the reply may assert, decided by CODE from state and evidence (never by the LLM)."""
    case_id = (state.get("case") or {}).get("id")
    if state.get("blocked"):
        return [dump(Claim(type="refused"))]
    if (state.get("next") or {}).get("action") == "ask_customer":
        return [dump(Claim(type="needs_more_info"))]
    elig = state.get("eligibility")
    if elig in ("not_eligible", "approval_required", "case_not_open"):
        kind = {"not_eligible": "escalation_declined", "approval_required": "approval_required",
                "case_not_open": "case_not_open"}[elig]
        return [dump(Claim(type=kind, case_id=case_id))]

    outcome = state.get("execute_outcome")
    if outcome is None:
        return [dump(Claim(type="refused"))] if state.get("not_found_order") else []
    if outcome == "unknown":
        return [dump(Claim(type="escalation_unconfirmed", case_id=case_id))]
    if outcome == "not_created":
        return [dump(Claim(type="escalation_not_submitted", case_id=case_id))]
    if outcome in ("not_found", "error"):
        return [dump(Claim(type="refused"))]

    esc = state["escalation"]
    esc_id = str(esc["id"])
    if outcome == "already_open":
        return [dump(Claim(type="escalation_already_open", escalation_id=esc_id, case_id=case_id))]
    # Only evidence about THIS escalation on THIS case counts (proof for another case never does).
    mine = [e for e in state.get("evidence", [])
            if e["kind"] == "escalation_status" and e["escalation_id"] == esc_id and e["case_id"] == case_id]
    if not mine:
        return []
    # "Simplification": the API accepted it, so the customer can be told it's done.
    kind = {"COMPLETED": "escalation_completed", "REQUESTED": "escalation_completed"}.get(mine[-1]["value"])
    return [dump(Claim(type=kind, escalation_id=esc_id, case_id=case_id))] if kind else []


# ======================================================================================= graph
def build_graph(deps: AgentDeps, checkpointer: Any = None):
    def api(state: AgentState) -> CaseApiClient:
        return deps.case_api(state["tenant_id"], state["customer_id"])

    # ------------------------------------------------------------------ guardrail
    def guardrail(state: AgentState) -> dict:
        reset = {"steps": 0, "next": None, "blocked": False, "not_found_order": None, "no_case": False,
                 "read_error": None, "eligibility": None, "days_late": None, "decision_reason": None,
                 "current_op": None, "execute_outcome": None, "escalation": None, "reply": None}
        if "operations" not in state:
            reset["operations"] = {}
        if _INJECTION.search(_latest_user(state)):
            reset["blocked"] = True
            reset["decisions"] = [{"action": "refuse", "reason": "prompt-injection pattern in the message"}]
        return reset

    # ------------------------------------------------------------------ plan (LLM)
    def plan(state: AgentState) -> dict:
        today = deps.today()
        if state["steps"] >= deps.max_steps:
            step = PlanStep(action="answer", reason="step budget reached")
        else:
            view = {
                "latest_message": _latest_user(state),
                "customer_messages": [m["content"] for m in state["messages"] if m["role"] == "user"],
                "orders": None if state.get("orders") is None
                          else [_order_summary(o, today) for o in state["orders"]],
                "order": _order_summary(state["order"], today) if state.get("order") else None,
                "case": state.get("case"),
                "policy": state.get("policy"),
                "not_found_order": state.get("not_found_order"),
                "no_case": state.get("no_case"),
                "read_error": state.get("read_error"),
                "steps_taken": state["steps"],
                "read_tools": list(READ_TOOLS),
            }
            step = deps.llm.plan(view)
            if step.action == "call_tool" and step.tool not in READ_TOOLS:
                # The LLM can only READ. Writes go through code-controlled nodes.
                step = PlanStep(action="answer", reason=f"blocked: '{step.tool}' is not a read tool")
        d = dump(step)
        return {"next": d, "steps": state["steps"] + 1, "decisions": [d]}

    # ------------------------------------------------------------------ run_read_tool
    def run_read_tool(state: AgentState) -> dict:
        step = state["next"]
        tool = step["tool"]
        # Identity ALWAYS comes from the session; anything the LLM put in args is dropped.
        args = {k: v for k, v in (step.get("args") or {}).items() if k not in IDENTITY_KEYS}
        client = api(state)
        update: dict[str, Any] = {}
        try:
            if tool == "list_orders":
                update["orders"] = client.list_orders()
            elif tool == "get_order":
                o = client.get_order(str(args["order_id"]))
                update.update(order=o, not_found_order=None)
                if (state.get("order") or {}).get("id") != o["id"]:
                    update.update(case=None, no_case=False)     # a different order: old case no longer applies
            elif tool == "get_cases":
                cases = client.get_cases(str(args["order_id"]))
                open_cases = [c for c in cases if c["status"] == "OPEN"]
                update.update(case=(open_cases or cases or [None])[0], no_case=not cases)
            elif tool == "search_policy":
                p = search_policy(deps.policies, str(args.get("query", "")), state["region"], deps.today())
                update["policy"] = p.summary() if p else None
        except NotFound:
            update.update(not_found_order=str(args.get("order_id")), order=None, case=None)
        except (Unavailable, CaseApiError):
            update["read_error"] = tool
        finally:
            client.close()
        update["tool_calls"] = client.trace
        return update

    # ------------------------------------------------------------------ check_eligibility (CODE)
    def check_eligibility(state: AgentState) -> dict:
        order, case, policy = state.get("order"), state.get("case"), state.get("policy")
        if not (order and case and policy):
            return {"eligibility": "missing_facts"}
        if case["status"] != "OPEN":
            return {"eligibility": "case_not_open"}
        days = business_days_late(date.fromisoformat(order["promisedDate"]), deps.today())
        limit = policy["min_business_days_late"]
        if order["status"] in ("DELIVERED", "CANCELLED") or days <= limit:
            return {"eligibility": "not_eligible", "days_late": days}
        approval_over = policy.get("approval_over_cents")
        if approval_over is not None and order["amountCents"] > approval_over:
            return {"eligibility": "approval_required", "days_late": days}
        reason = (f"Order {order['id']} is {days} business days late "
                  f"(policy {policy['ref']}: escalate after more than {limit}).")
        return {"eligibility": "eligible", "days_late": days, "decision_reason": reason}

    # ------------------------------------------------------------------ prepare_action
    def prepare_action(state: AgentState) -> dict:
        # Its own node: LangGraph checkpoints after each node, so the id is saved BEFORE the write.
        fingerprint = f"escalate:{state['case']['id']}"
        ops = dict(state.get("operations") or {})
        existing = ops.get(fingerprint)
        if existing and existing["status"] != "FAILED":
            return {"current_op": fingerprint}                     # same action → reuse its id
        ops[fingerprint] = {"id": str(uuid.uuid4()), "status": "NEW"}  # new action → new id
        return {"operations": ops, "current_op": fingerprint}

    # ------------------------------------------------------------------ execute
    def execute(state: AgentState) -> dict:
        op_id = state["operations"][state["current_op"]]["id"]
        client = api(state)
        update: dict[str, Any] = {}
        try:
            attempts = len(deps.retry_delays) + 1
            for attempt in range(attempts):
                try:
                    esc = client.request_escalation(state["case"]["id"], op_id, state["decision_reason"],
                                                    state["policy"]["ref"])
                    update = {"execute_outcome": "accepted", "escalation": esc,
                              "evidence": [_escalation_evidence(esc, "request_escalation")]}
                    break
                except Unavailable:
                    if attempt < attempts - 1:
                        deps.sleep(deps.retry_delays[attempt])       # same op_id on the next attempt
                except AlreadyOpen as e:
                    try:
                        esc = client.get_escalation(e.existing_escalation_id)
                        update = {"execute_outcome": "already_open", "escalation": esc,
                                  "evidence": [_escalation_evidence(esc, "get_escalation_status")]}
                    except (Unavailable, CaseApiError):
                        update = {"execute_outcome": "already_open",
                                  "escalation": {"id": e.existing_escalation_id, "caseId": state["case"]["id"]}}
                    break
                except CaseNotOpen:
                    update = {"execute_outcome": None, "eligibility": "case_not_open"}
                    break
                except NotFound:
                    update = {"execute_outcome": "not_found"}
                    break
                except (OperationConflict, CaseApiError):
                    update = {"execute_outcome": "error"}
                    break
            else:
                update = {"execute_outcome": "unknown"}               # NOT "failed": the write may have happened
        finally:
            client.close()
        update["tool_calls"] = client.trace
        return update

    # ------------------------------------------------------------------ reconcile
    def reconcile(state: AgentState) -> dict:
        op_id = state["operations"][state["current_op"]]["id"]
        client = api(state)
        try:
            esc = client.find_escalation_by_operation(op_id)
            if esc:
                update = {"execute_outcome": "accepted", "escalation": esc,
                          "evidence": [_escalation_evidence(esc, "find_escalation_by_operation")]}
            else:
                update = {"execute_outcome": "not_created"}           # confirmed: nothing was created
        except (Unavailable, CaseApiError):
            update = {"execute_outcome": "unknown"}                   # still can't tell: say so honestly
        finally:
            client.close()
        update["tool_calls"] = client.trace
        return update

    # ------------------------------------------------------------------ verify
    def verify(state: AgentState) -> dict:
        esc_id = str(state["escalation"]["id"])
        client = api(state)
        evidence: list[dict] = []
        try:
            for i, delay in enumerate(deps.poll_delays):
                try:
                    dto = client.get_escalation(esc_id)
                except (Unavailable, CaseApiError):
                    dto = None
                if dto:
                    evidence.append(_escalation_evidence(dto, "get_escalation_status"))
                    if dto["status"] in ("COMPLETED", "FAILED"):
                        break
                if i < len(deps.poll_delays) - 1:
                    deps.sleep(delay)
        finally:
            client.close()
        return {"evidence": evidence, "tool_calls": client.trace}

    # ------------------------------------------------------------------ compose_reply
    def compose_reply(state: AgentState) -> dict:
        order, case, policy = state.get("order") or {}, state.get("case") or {}, state.get("policy") or {}
        op = (state.get("operations") or {}).get(state.get("current_op") or "", {})
        refusal = None
        if state.get("blocked"):
            refusal = "Sorry, I can't help with that request."
        elif state.get("not_found_order"):
            refusal = f"I couldn't find order {state['not_found_order']} on your account."
        elif state.get("execute_outcome") in ("not_found", "error"):
            refusal = "Sorry, something went wrong and I couldn't complete that request."
        facts = {
            "order_id": order.get("id"), "order_description": order.get("description"),
            "order_status": order.get("status"), "promised_date": order.get("promisedDate"),
            "case_id": case.get("id"), "policy_ref": policy.get("ref"),
            "policy_min_days": policy.get("min_business_days_late"), "days_late": state.get("days_late"),
            "escalation_id": str(state["escalation"]["id"]) if state.get("escalation") else None,
            "operation_id": op.get("id"), "question": (state.get("next") or {}).get("question"),
            "refusal": refusal,
        }
        allowed = allowed_claims(state)
        draft = deps.llm.write_reply({"allowed_claims": allowed, "facts": facts,
                                      "customer_message": _latest_user(state)})
        reply = {"text": draft.text, "claims": [dump(c) for c in draft.claims],
                 "policy_cited": draft.policy_cited, "allowed_claims": allowed,
                 "created_at": utcnow().isoformat()}
        return {"reply": reply, "messages": [{"role": "assistant", "content": draft.text}]}

    # ------------------------------------------------------------------ wiring
    g = StateGraph(AgentState)
    for name, fn in [("guardrail", guardrail), ("plan", plan), ("run_read_tool", run_read_tool),
                     ("check_eligibility", check_eligibility), ("prepare_action", prepare_action),
                     ("execute", execute), ("reconcile", reconcile), ("verify", verify),
                     ("compose_reply", compose_reply)]:
        g.add_node(name, fn)

    g.add_edge(START, "guardrail")
    g.add_conditional_edges("guardrail", lambda s: "compose_reply" if s["blocked"] else "plan")
    g.add_conditional_edges("plan", lambda s: {
        "call_tool": "run_read_tool", "ask_customer": "compose_reply", "answer": "compose_reply",
        "propose_escalation": "check_eligibility"}[s["next"]["action"]])
    g.add_edge("run_read_tool", "plan")                       # the agent loop: read, then decide again
    g.add_conditional_edges("check_eligibility", lambda s: {
        "eligible": "prepare_action", "missing_facts": "plan"}.get(s["eligibility"], "compose_reply"))
    g.add_edge("prepare_action", "execute")
    g.add_conditional_edges("execute", lambda s: {
        "accepted": "verify", "already_open": "verify", "unknown": "reconcile"}.get(s.get("execute_outcome"),
                                                                                    "compose_reply"))
    g.add_conditional_edges("reconcile", lambda s: "verify" if s["execute_outcome"] == "accepted"
                            else "compose_reply")
    g.add_edge("verify", "compose_reply")
    g.add_edge("compose_reply", END)
    return g.compile(checkpointer=checkpointer or MemorySaver())
