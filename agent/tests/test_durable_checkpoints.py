"""S14 with REAL PostgreSQL: a paused conversation survives the agent process going away.

Two independent checkpointers (separate connection pools) play two processes: the first pauses the
conversation, the second, which shares nothing with it but the database, resumes it.
Runs when CASEGUARD_TEST_PG is set, e.g. postgresql://caseguard:caseguard@localhost:5432/caseguard_agent_test
"""
from __future__ import annotations

import os
import uuid

import pytest

from caseguard_agent.case_api import CaseApiClient
from caseguard_agent.checkpoints import make_checkpointer
from caseguard_agent.graph import AgentDeps, build_graph
from caseguard_agent.llm import ScriptedLLM
from caseguard_agent.policies import load_policies
from caseguard_agent.runner import AgentRunner

from .conftest import ASHA

PG = os.getenv("CASEGUARD_TEST_PG")
pytestmark = pytest.mark.skipif(not PG, reason="set CASEGUARD_TEST_PG to a PostgreSQL URL")


def process(api, today) -> AgentRunner:
    deps = AgentDeps(llm=ScriptedLLM(),
                     case_api=lambda t, c: CaseApiClient("http://case-api", t, c, transport=api.transport()),
                     policies=load_policies(), today=lambda: today,
                     retry_delays=(0, 0), poll_delays=(0, 0, 0, 0), sleep=lambda s: None)
    return AgentRunner(build_graph(deps, checkpointer=make_checkpointer(PG)))


def test_paused_conversation_survives_a_new_process(api, today):
    conv = str(uuid.uuid4())
    first = process(api, today)
    r1 = first.turn(conv, ASHA, "Order 1099 is late, please escalate it.")
    assert r1["waiting_for_approval"]
    approval = api.latest_approval()
    first.graph.checkpointer.conn.close()          # process 1 is gone, connections and all

    second = process(api, today)
    assert second.waiting_for_approval(conv) == approval["id"]
    assert [m["role"] for m in second.messages(conv)] == ["user", "assistant"], "history survived too"

    api.decide(approval["id"], "APPROVED")
    r2 = second.resume(conv, {})
    assert [c["type"] for c in r2["reply"]["claims"]] == ["escalation_completed"]
    assert api.posts[0]["operation_id"] == approval["operationId"], "same operation id after the restart"
    assert len(api.approvals) == 1
