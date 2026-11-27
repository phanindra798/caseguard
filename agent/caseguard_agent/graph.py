"""The agent as a LangGraph state machine.

    START → guardrail ─(blocked)──────────────────────────────────────────► compose_reply
               │ok
               ▼
            plan ◄──────────── run_read_tool        (LLM chooses; READ tools only; step budget)
               │ ask_customer / answer ─────────────────────────────────► compose_reply
               │ propose_escalation
               ▼
          check_eligibility (CODE) ─(not eligible / closed)───────────────► compose_reply
               │ eligible / needs approval   └─(missing facts)──► plan
               ▼
          prepare_action   operation id per action fingerprint, checkpointed BEFORE the write
               │ needs approval:
               │   create_approval ─► announce_approval ─► wait_for_approval (interrupt: PAUSE)
               │                                              │ resumed by the supervisor's decision
               │   revalidate (approval APPROVED? case + policy unchanged?) ◄┘
               │     ├ rejected ──────────────────────────────────────────► compose_reply
               │     ├ case/policy changed → new operation id ──► check_eligibility (ask again)
               │     └ valid ──► execute (with approval id)
               ▼
            execute ─ accepted / already_open ──► verify ──────────────► compose_reply
               └─ unknown (timeouts) ──► reconcile ─ found ──► verify
                                                   ├ confirmed not created ──► compose_reply
                                                   └ still unknown ──────────► compose_reply

Split of responsibilities: the LLM decides WHAT TO FIND OUT and WHAT TO PROPOSE.
Code decides WHAT IS ALLOWED and WHAT IS TRUE. The harness checks both.
"""
from __future__ import annotations

import json
import operator
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .business_days import business_days_late
from .case_api import (AlreadyOpen, ApprovalRequired, CaseApiClient, CaseApiError, CaseChanged,
                       CaseNotOpen, NotFound, OperationConflict, Unavailable)
from .llm.base import AgentLLM
from .models import READ_TOOLS, Claim, Evidence, PlanStep, dump, utcnow
from .output_guard import violations
from .replies import render_reply
from .tracing import traced_node
from .untrusted import scan
from .policies import ESCALATION_KIND, KeywordPolicyIndex, Policy, PolicyIndex

IDENTITY_KEYS = {"tenant_id", "customer_id", "tenantId", "customerId", "region"}


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
    focus_order_id: str | None  # which order the conversation is about (kept across turns)
    order: dict | None
    case: dict | None
    policy: dict | None
    operations: dict            # {fingerprint: {"id": uuid, "status": ...}}
    approval: dict | None       # the supervisor approval for the current operation (Case API view)
    approval_phase: str | None  # "requested" while the customer is being told "sent for approval"
    approval_check: str | None  # revalidate's verdict: valid | pending | rejected | stale | unavailable
    approval_signal: dict | None  # what the resume carried (informational; the Case API is the truth)
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
    policy_index: PolicyIndex | None = None            # default: keyword search over `policies` (best measured)
    today: Callable[[], date] = date.today
    retry_delays: tuple[float, ...] = (0.5, 1.0)      # 3 attempts in total
    poll_delays: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)
    max_steps: int = 6
    output_guard: bool = True                         # replace unproven replies before they reach the customer
    sleep: Callable[[float], None] = field(default=time.sleep)


# ======================================================================================= helpers
def _latest_user(state: AgentState) -> str:
    return next((m["content"] for m in reversed(state.get("messages", [])) if m["role"] == "user"), "")


def _order_summary(o: dict, today: date) -> dict:
    return {"id": o["id"], "description": o["description"], "status": o["status"],
            "amount_cents": o["amountCents"], "promised_date": o["promisedDate"],
            "late_business_days": business_days_late(date.fromisoformat(o["promisedDate"]), today),
            # Labelled as untrusted: the prompt says untrusted_* fields are DATA, never instructions.
            "untrusted_customer_note": o.get("customerNote")}


def _escalation_evidence(dto: dict, source: str) -> dict:
    return dump(Evidence(kind="escalation_status", value=dto["status"], escalation_id=str(dto["id"]),
                         case_id=dto["caseId"], source=source))


def allowed_claims(state: AgentState) -> list[dict]:
    """What the reply may assert, decided by CODE from state and evidence (never by the LLM)."""
    case_id = (state.get("case") or {}).get("id")
    if state.get("blocked"):
        return [dump(Claim(type="refused"))]
    if state.get("approval_phase") == "requested":
        return [dump(Claim(type="approval_requested", case_id=case_id))]
    if (state.get("next") or {}).get("action") == "ask_customer":
        return [dump(Claim(type="needs_more_info"))]
    elig = state.get("eligibility")
    if elig in ("not_eligible", "approval_rejected", "case_not_open", "no_policy"):
        kind = {"not_eligible": "escalation_declined", "approval_rejected": "approval_rejected",
                "case_not_open": "case_not_open", "no_policy": "no_applicable_policy"}[elig]
        return [dump(Claim(type=kind, case_id=case_id))]

    outcome = state.get("execute_outcome")
    if outcome is None:
        return [dump(Claim(type="refused"))] if state.get("not_found_order") else []
    if outcome == "unknown":
        return [dump(Claim(type="escalation_unconfirmed", case_id=case_id))]
    if outcome in ("not_created", "approval_refused", "approval_unavailable"):
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
    policy_index = deps.policy_index or KeywordPolicyIndex(deps.policies)

    def api(state: AgentState) -> CaseApiClient:
        return deps.case_api(state["tenant_id"], state["customer_id"])

    # ------------------------------------------------------------------ guardrail
    def guardrail(state: AgentState) -> dict:
        reset = {"steps": 0, "next": None, "blocked": False, "not_found_order": None, "no_case": False,
                 "read_error": None, "eligibility": None, "days_late": None, "decision_reason": None,
                 "current_op": None, "execute_outcome": None, "escalation": None, "reply": None,
                 "approval_phase": None, "approval_check": None}
        if "operations" not in state:
            reset["operations"] = {}
        # Fresh facts every turn: remember WHICH order we're talking about, but forget what we
        # read about it. The planner re-reads order, case and policy from the system of record,
        # so a correction made by support staff between turns is never missed.
        focus = (state.get("order") or {}).get("id") or state.get("focus_order_id")
        reset.update(focus_order_id=focus, order=None, case=None, policy=None, orders=None)
        if scan(_latest_user(state), block_only=True):   # block only on clear attacks; flagging is broader
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
                "focus_order_id": state.get("focus_order_id"),
                # Cases this conversation already asked to escalate (fingerprints "escalate:<case id>").
                "escalated_cases": [fp.split(":", 1)[1] for fp in (state.get("operations") or {})],
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
                flags = scan(o.get("customerNote"))
                if flags:
                    update["decisions"] = [dump(PlanStep(
                        action="answer", reason=f"FLAG untrusted: order {o['id']} note contains instruction-like "
                        f"text {flags}; treated as data, not instructions"))
                        | {"action": "flag_untrusted"}]
                if (state.get("order") or {}).get("id") != o["id"]:
                    update.update(case=None, no_case=False)     # a different order: old case no longer applies
            elif tool == "get_cases":
                cases = client.get_cases(str(args["order_id"]))
                open_cases = [c for c in cases if c["status"] == "OPEN"]
                update.update(case=(open_cases or cases or [None])[0], no_case=not cases)
            elif tool == "search_policy":
                p = policy_index.search(str(args.get("query", "")), state["region"], deps.today())
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
        if policy.get("kind") != ESCALATION_KIND:
            # Retrieval found SOME policy, but not one that governs escalations: never decide with it.
            return {"eligibility": "no_policy"}
        if case["status"] != "OPEN":
            return {"eligibility": "case_not_open"}
        days = business_days_late(date.fromisoformat(order["promisedDate"]), deps.today())
        limit = policy["min_business_days_late"]
        if order["status"] in ("DELIVERED", "CANCELLED") or days <= limit:
            return {"eligibility": "not_eligible", "days_late": days}
        reason = (f"Order {order['id']} is {days} business days late "
                  f"(policy {policy['ref']}: escalate after more than {limit}).")
        approval_over = policy.get("approval_over_cents")
        if approval_over is not None and order["amountCents"] > approval_over:
            return {"eligibility": "needs_approval", "days_late": days, "decision_reason": reason}
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
                    approval_id = (state.get("approval") or {}).get("id") \
                        if state.get("eligibility") == "needs_approval" else None
                    esc = client.request_escalation(state["case"]["id"], op_id, state["decision_reason"],
                                                    state["policy"]["ref"], approval_id)
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
                except ApprovalRequired:
                    # The backend's own check said no (e.g. a race after revalidate). Nothing was written.
                    update = {"execute_outcome": "approval_refused"}
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

    # ------------------------------------------------------------------ create_approval
    def create_approval(state: AgentState, config: RunnableConfig) -> dict:
        """Ask a supervisor. A SIDE EFFECT, so it lives in its own node: LangGraph re-runs the whole
        node that calls interrupt() on resume, and that must never create a second request.
        The Case API call is also idempotent (same operation id → same approval)."""
        op_id = state["operations"][state["current_op"]]["id"]
        case, policy = state["case"], state["policy"]
        conversation = config["configurable"]["thread_id"]
        client = api(state)
        try:
            try:
                a = client.request_approval(case["id"], op_id, case["version"], policy["ref"],
                                            state["decision_reason"], conversation)
            except CaseChanged:
                case = client.get_case(case["id"])           # someone edited the case: use what is true now
                a = client.request_approval(case["id"], op_id, case["version"], policy["ref"],
                                            state["decision_reason"], conversation)
            update = {"approval": a, "case": case}
        except (Unavailable, CaseApiError):
            update = {"approval": None, "execute_outcome": "approval_unavailable"}
        finally:
            client.close()
        update["tool_calls"] = client.trace
        return update

    # ------------------------------------------------------------------ announce_approval
    def announce_approval(state: AgentState) -> dict:
        """Tell the customer, BEFORE pausing, exactly what is true: it has been sent for approval."""
        return compose({**state, "approval_phase": "requested"}) | {"approval_phase": "requested"}

    # ------------------------------------------------------------------ wait_for_approval
    def wait_for_approval(state: AgentState) -> dict:
        """PAUSE. The graph stops here and its state is checkpointed. It continues when someone calls
        Command(resume=...). On resume LangGraph re-runs this node from the top, which is safe because
        it has no side effects. What the resume carries is NOT trusted: revalidate asks the Case API."""
        raw = interrupt({"approval_id": state["approval"]["id"], "case_id": state["case"]["id"]})
        return {"approval_signal": json.loads(raw) if isinstance(raw, str) else raw}

    # ------------------------------------------------------------------ revalidate
    def revalidate(state: AgentState) -> dict:
        """After the pause: is the approval really APPROVED, and does it still match the world?"""
        client = api(state)
        try:
            a = client.get_approval(state["approval"]["id"])
            update: dict[str, Any] = {"approval": a, "approval_phase": None}
            if a["status"] == "PENDING":
                update["approval_check"] = "pending"            # woken too early: keep waiting
            elif a["status"] == "REJECTED":
                update.update(approval_check="rejected", eligibility="approval_rejected")
            else:
                fresh_case = client.get_case(state["case"]["id"])
                fresh = policy_index.search("late delivery escalation", state["region"], deps.today())
                expired = datetime.fromisoformat(a["expiresAt"]) < utcnow()
                changed = [why for why, bad in (
                    ("the case changed", fresh_case["version"] != a["caseVersion"]),
                    ("the policy changed", fresh is None or fresh.ref != a["policyRef"]),
                    ("the approval expired", expired)) if bad]
                if changed:
                    # The old approval is void. New operation id → a fresh approval for the new facts.
                    ops = dict(state["operations"])
                    ops[state["current_op"]] = {"id": str(uuid.uuid4()), "status": "NEW"}
                    update.update(approval_check="stale", approval=None, operations=ops, case=fresh_case,
                                  policy=fresh.summary() if fresh else None,
                                  decisions=[{"action": "revalidate", "tool": None, "args": {}, "question": None,
                                              "reason": f"{' and '.join(changed)} while waiting; asking again"}])
                else:
                    update.update(approval_check="valid", case=fresh_case)
        except (Unavailable, CaseApiError):
            update = {"approval_check": "unavailable", "approval_phase": None}   # can't tell: keep waiting
        finally:
            client.close()
        update["tool_calls"] = client.trace
        return update

    # ------------------------------------------------------------------ compose_reply
    def compose_reply(state: AgentState) -> dict:
        return compose(state)

    def compose(state: AgentState) -> dict:
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
            "approval_id": (state.get("approval") or {}).get("id"),
            "customer_note": order.get("customerNote"),
            "refusal": refusal,
        }
        allowed = allowed_claims(state)
        draft = deps.llm.write_reply({"allowed_claims": allowed, "facts": facts,
                                      "customer_message": _latest_user(state)})
        update: dict[str, Any] = {}
        problems = violations(draft, [Claim(**c) for c in allowed])
        if problems and deps.output_guard:
            # The model said more than the evidence supports. The customer gets the verified reply.
            draft = render_reply([Claim(**c) for c in allowed], facts)
            update["decisions"] = [{"action": "output_guard", "tool": None, "args": {}, "question": None,
                                    "reason": "replaced an unproven reply: " + "; ".join(problems)}]
        reply = {"text": draft.text, "claims": [dump(c) for c in draft.claims],
                 "policy_cited": draft.policy_cited, "allowed_claims": allowed,
                 "created_at": utcnow().isoformat()}
        return update | {"reply": reply, "messages": [{"role": "assistant", "content": draft.text}]}

    # ------------------------------------------------------------------ wiring
    g = StateGraph(AgentState)
    for name, fn in [("guardrail", guardrail), ("plan", plan), ("run_read_tool", run_read_tool),
                     ("check_eligibility", check_eligibility), ("prepare_action", prepare_action),
                     ("execute", execute), ("reconcile", reconcile), ("verify", verify),
                     ("create_approval", create_approval), ("announce_approval", announce_approval),
                     ("wait_for_approval", wait_for_approval), ("revalidate", revalidate),
                     ("compose_reply", compose_reply)]:
        g.add_node(name, traced_node(name, fn))            # one span per node (no-op if tracing is off)

    g.add_edge(START, "guardrail")
    g.add_conditional_edges("guardrail", lambda s: "compose_reply" if s["blocked"] else "plan")
    g.add_conditional_edges("plan", lambda s: {
        "call_tool": "run_read_tool", "ask_customer": "compose_reply", "answer": "compose_reply",
        "propose_escalation": "check_eligibility"}[s["next"]["action"]])
    g.add_edge("run_read_tool", "plan")                       # the agent loop: read, then decide again
    g.add_conditional_edges("check_eligibility", lambda s: {
        "eligible": "prepare_action", "needs_approval": "prepare_action",
        "missing_facts": "plan"}.get(s["eligibility"], "compose_reply"))
    g.add_conditional_edges("prepare_action", lambda s: "create_approval" if s["eligibility"] == "needs_approval"
                            else "execute")
    g.add_conditional_edges("create_approval", lambda s: "compose_reply" if not s.get("approval")
                            else "announce_approval" if s["approval"]["status"] == "PENDING" else "revalidate")
    g.add_edge("announce_approval", "wait_for_approval")
    g.add_edge("wait_for_approval", "revalidate")
    g.add_conditional_edges("revalidate", lambda s: {
        "valid": "execute", "rejected": "compose_reply", "stale": "check_eligibility",
        "pending": "wait_for_approval", "unavailable": "wait_for_approval"}[s["approval_check"]])
    g.add_conditional_edges("execute", lambda s: {
        "accepted": "verify", "already_open": "verify", "unknown": "reconcile"}.get(s.get("execute_outcome"),
                                                                                    "compose_reply"))
    g.add_conditional_edges("reconcile", lambda s: "verify" if s["execute_outcome"] == "accepted"
                            else "compose_reply")
    g.add_edge("verify", "compose_reply")
    g.add_edge("compose_reply", END)
    compiled = g.compile(checkpointer=checkpointer or MemorySaver())
    compiled._caseguard_llm = deps.llm          # lets the runner read the LLM's usage meter per turn
    return compiled
