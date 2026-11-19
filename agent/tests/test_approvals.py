"""Phase 2.1: supervisor approval with pause/resume (S12, S13, S14) against the fake Case API.

Order 1099 is $750, over the $500 limit in policy ESC-3@v2.
"""
from __future__ import annotations

import uuid

from caseguard_agent.case_api import CaseApiClient
from caseguard_agent.graph import AgentDeps, build_graph
from caseguard_agent.llm import ScriptedLLM
from caseguard_agent.policies import load_policies
from caseguard_agent.runner import AgentRunner

from .conftest import ASHA, make_agent

ASK = "Order 1099 is late, please escalate it."


def claim_types(r):
    return [c["type"] for c in r["reply"]["claims"]]


def start(agent):
    conv = str(uuid.uuid4())
    return conv, agent.turn(conv, ASHA, ASK)


# ------------------------------------------------------------------------------------- S12
def test_s12_expensive_order_pauses_for_approval_then_completes(agent, api):
    conv, r1 = start(agent)
    assert claim_types(r1) == ["approval_requested"]
    assert r1["waiting_for_approval"] == api.latest_approval()["id"]
    assert api.posts == [], "nothing escalated while waiting"
    approval = api.latest_approval()
    assert approval["caseVersion"] == 0 and approval["policyRef"] == "ESC-3@v2"
    assert approval["conversationRef"] == conv, "the approval knows which conversation to resume"

    api.decide(approval["id"], "APPROVED")
    r2 = agent.resume(conv, {"approval_id": approval["id"]})

    assert claim_types(r2) == ["escalation_completed"]
    assert r2["waiting_for_approval"] is None
    assert len(api.posts) == 1
    assert api.posts[0]["operation_id"] == approval["operationId"], "the approved operation is the one executed"
    assert len(api.approvals) == 1, "resuming never creates a second approval"
    assert [t["tool"] for t in r2["tool_calls"]][:3] == ["get_approval", "get_case", "request_escalation"]


def test_rejected_approval_is_reported_and_nothing_is_escalated(agent, api):
    conv, _ = start(agent)
    api.decide(api.latest_approval()["id"], "REJECTED")
    r = agent.resume(conv, {})
    assert claim_types(r) == ["approval_rejected"]
    assert api.escalations == {}


def test_resume_payload_is_not_trusted(agent, api):
    """Someone resumes claiming 'APPROVED' while the Case API still says PENDING: keep waiting."""
    conv, _ = start(agent)
    r = agent.resume(conv, {"decision": "APPROVED"})          # a lie
    assert r["waiting_for_approval"] == api.latest_approval()["id"]
    assert api.posts == []


def test_customer_message_while_waiting_keeps_the_pause(agent, api):
    conv, _ = start(agent)
    r = agent.turn(conv, ASHA, "Any news?")
    assert claim_types(r) == ["approval_requested"]
    assert r["tool_calls"] == [] and r["waiting_for_approval"]
    api.decide(api.latest_approval()["id"], "APPROVED")
    assert claim_types(agent.resume(conv, {})) == ["escalation_completed"]


# ------------------------------------------------------------------------------------- S13
def test_s13_case_changed_while_waiting_voids_the_approval_and_asks_again(agent, api):
    conv, _ = start(agent)
    first = api.latest_approval()
    api.decide(first["id"], "APPROVED")
    api.edit_case("C-1099")                                   # someone updated the case meanwhile

    r = agent.resume(conv, {})
    assert claim_types(r) == ["approval_requested"], "a fresh approval was requested"
    assert any(d["action"] == "revalidate" and "case changed" in d["reason"] for d in r["decisions"])
    second = api.latest_approval()
    assert second["id"] != first["id"]
    assert second["operationId"] != first["operationId"], "new facts → new operation id"
    assert second["caseVersion"] == 1
    assert api.posts == [], "the stale approval was never used"

    api.decide(second["id"], "APPROVED")
    r2 = agent.resume(conv, {})
    assert claim_types(r2) == ["escalation_completed"]
    assert [p["operation_id"] for p in api.posts] == [second["operationId"]]


# ------------------------------------------------------------------------------------- S14
def test_s14_agent_restart_while_waiting_resumes_with_the_same_operation(api, today):
    from langgraph.checkpoint.memory import MemorySaver
    shared = MemorySaver()          # stands in for PostgreSQL: the only thing that survives the "restart"

    def new_process() -> AgentRunner:
        deps = AgentDeps(llm=ScriptedLLM(),
                         case_api=lambda t, c: CaseApiClient("http://case-api", t, c, transport=api.transport()),
                         policies=load_policies(), today=lambda: today,
                         retry_delays=(0, 0), poll_delays=(0, 0, 0, 0), sleep=lambda s: None)
        return AgentRunner(build_graph(deps, checkpointer=shared))

    before = new_process()
    conv, _ = start(before)
    approval = api.latest_approval()
    del before                                             # the agent process dies

    after = new_process()                                  # a brand-new process, same checkpoint store
    assert after.waiting_for_approval(conv) == approval["id"]
    api.decide(approval["id"], "APPROVED")
    r = after.resume(conv, {})
    assert claim_types(r) == ["escalation_completed"]
    assert api.posts[0]["operation_id"] == approval["operationId"]
    assert len(api.approvals) == 1


def test_cheap_orders_still_need_no_approval(agent, api):
    _, r = agent.turn(str(uuid.uuid4()), ASHA, "Order 1042 is late, please escalate it."), None
    assert api.approvals == {}
