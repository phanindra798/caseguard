"""End-to-end: the agent against the REAL Case API, Kafka and escalation worker.

Run with scripts/e2e-agent.sh (starts everything on a fresh database), or by hand:
    CASEGUARD_E2E=1 CASE_API_URL=http://localhost:8081 pytest -m e2e
Tests run in file order on purpose: the second one relies on the first one's escalation.
"""
from __future__ import annotations

import os
import uuid

import pytest

from caseguard_agent.case_api import CaseApiClient
from caseguard_agent.graph import AgentDeps, build_graph
from caseguard_agent.llm import ScriptedLLM
from caseguard_agent.policies import load_policies
from caseguard_agent.runner import AgentRunner, Session

pytestmark = [pytest.mark.e2e,
              pytest.mark.skipif(os.getenv("CASEGUARD_E2E") != "1", reason="set CASEGUARD_E2E=1")]

ASHA = Session(tenant_id="acme", customer_id="cust-A1", region="US")
URL = os.getenv("CASE_API_URL", "http://localhost:8081")


@pytest.fixture(scope="module")
def agent() -> AgentRunner:
    deps = AgentDeps(llm=ScriptedLLM(),
                     case_api=lambda t, c: CaseApiClient(URL, t, c),
                     policies=load_policies(),
                     poll_delays=(0.5, 1, 2, 3, 4, 5))       # real worker: give it time
    return AgentRunner(build_graph(deps))


def ask(agent, message, conversation=None):
    return agent.turn(conversation or str(uuid.uuid4()), ASHA, message)


def types(r):
    return [c["type"] for c in r["reply"]["claims"]]


def test_real_escalation_flows_through_kafka_and_worker(agent):
    r = ask(agent, "Order 1042 is late, please escalate it.")
    assert types(r) == ["escalation_completed"], r["reply"]
    statuses = [e["value"] for e in r["evidence"]]
    assert statuses[0] == "REQUESTED" and statuses[-1] == "COMPLETED", "verified by polling the real API"


def test_second_conversation_sees_the_existing_escalation(agent):
    r = ask(agent, "Order 1042 is still late. Escalate it please!")
    assert types(r) == ["escalation_already_open"]
    assert [t["status"] for t in r["tool_calls"] if t["tool"] == "request_escalation"] == [409]


def test_not_eligible_order_is_declined_without_a_write(agent):
    r = ask(agent, "Order 1043 is late, can you escalate?")
    assert types(r) == ["escalation_declined"]
    assert "request_escalation" not in [t["tool"] for t in r["tool_calls"]]


def test_other_tenants_order_is_invisible(agent):
    r = ask(agent, "Order 2001 is late, please escalate it.")
    assert types(r) == ["refused"]
    assert [t["status"] for t in r["tool_calls"]] == [404]


def test_ambiguous_request_uses_the_real_order_list(agent):
    r = ask(agent, "My order is late, please escalate it.")
    assert types(r) == ["needs_more_info"]
    assert [t["tool"] for t in r["tool_calls"]] == ["list_orders"]
