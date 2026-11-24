"""Token and cost accounting: real counts from live responses, estimates from the scripted brain."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

from caseguard_agent.llm.live import LiveLLM
from caseguard_agent.models import PlanStep
from caseguard_agent.usage import Usage, UsageMeter, estimate_tokens

from .conftest import ASHA


class FakeRunnable:
    """Stands in for LangChain's structured-output runnable with include_raw=True."""
    def __init__(self, parsed, input_tokens, output_tokens):
        self.out = {"raw": SimpleNamespace(usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens}),
                    "parsed": parsed, "parsing_error": None}

    def invoke(self, messages):
        return self.out


def live_with(planner):
    llm = object.__new__(LiveLLM)                       # skip __init__: no provider, no network
    llm.meter, llm._planner = UsageMeter(), planner
    return llm


def test_live_records_real_token_counts_from_the_response():
    llm = live_with(FakeRunnable(PlanStep(action="answer", reason="r"), 1200, 35))
    assert llm.plan({"x": 1}).action == "answer"
    u = llm.meter.snapshot()
    assert (u.calls, u.input_tokens, u.output_tokens, u.estimated) == (1, 1200, 35, False)


def test_live_refuses_output_that_does_not_match_the_schema():
    bad = FakeRunnable(None, 10, 5)
    bad.out["parsing_error"] = "not JSON"
    llm = live_with(bad)
    try:
        llm.plan({})
        raise AssertionError("should have raised")
    except ValueError as e:
        assert "did not match the schema" in str(e)
    assert llm.meter.snapshot().calls == 1, "a failed call still costs tokens and is counted"


def test_cost_needs_configured_prices(monkeypatch):
    u = Usage(calls=2, input_tokens=2_000_000, output_tokens=500_000)
    monkeypatch.delenv("CASEGUARD_PRICE_INPUT_PER_1M", raising=False)
    monkeypatch.delenv("CASEGUARD_PRICE_OUTPUT_PER_1M", raising=False)
    assert u.cost_usd() is None, "no hardcoded prices: unknown until configured"
    monkeypatch.setenv("CASEGUARD_PRICE_INPUT_PER_1M", "1.0")
    monkeypatch.setenv("CASEGUARD_PRICE_OUTPUT_PER_1M", "4.0")
    assert u.cost_usd() == 2.0 + 2.0


def test_scripted_turn_reports_estimated_usage_of_the_real_prompts(agent):
    r = agent.turn(str(uuid.uuid4()), ASHA, "Order 1042 is late, please escalate it.")
    u = r["usage"]
    assert u["estimated"] is True
    assert u["llm_calls"] == 5, "4 planner steps + 1 reply"
    assert u["input_tokens"] > 1000, "the prompts (instructions + gathered facts) are what a live model would read"


def test_estimate_tokens():
    assert estimate_tokens("") == 0 and estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2
