from __future__ import annotations

from datetime import date

import pytest

from caseguard_agent.case_api import CaseApiClient
from caseguard_agent.graph import AgentDeps, build_graph
from caseguard_agent.llm import ScriptedLLM
from caseguard_agent.policies import load_policies
from caseguard_agent.runner import AgentRunner, Session

from .fake_case_api import FakeCaseApi

ASHA = Session(tenant_id="acme", customer_id="cust-A1", region="US")


@pytest.fixture
def today() -> date:
    return date.today()


@pytest.fixture
def api(today) -> FakeCaseApi:
    return FakeCaseApi(today)


def make_agent(api: FakeCaseApi, today: date, llm=None) -> AgentRunner:
    deps = AgentDeps(
        llm=llm or ScriptedLLM(),
        case_api=lambda t, c: CaseApiClient("http://case-api", t, c, transport=api.transport()),
        policies=load_policies(),
        today=lambda: today,
        retry_delays=(0, 0), poll_delays=(0, 0, 0, 0), sleep=lambda s: None)   # no real waiting in tests
    return AgentRunner(build_graph(deps))


@pytest.fixture
def agent(api, today) -> AgentRunner:
    return make_agent(api, today)
