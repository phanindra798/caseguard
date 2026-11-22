"""Policy retrieval: the filters always hold, and quality never silently gets worse."""
from __future__ import annotations

import os

import pytest

from caseguard_agent.embeddings import make_embedder
from caseguard_agent.policies import (HybridPolicyIndex, InMemoryPolicyIndex, KeywordPolicyIndex,
                                      PgVectorPolicyIndex, load_policies)
from caseguard_agent.retrieval_eval import evaluate, load_cases

POLICIES = load_policies()
CASES = load_cases()


@pytest.mark.parametrize("index", [KeywordPolicyIndex(POLICIES), InMemoryPolicyIndex(POLICIES, make_embedder("hashing")),
                                   HybridPolicyIndex(POLICIES, make_embedder("hashing"))], ids=lambda i: i.name)
def test_region_and_date_filters_always_hold(index):
    """Whatever the ranking does, an expired or other-region policy must never be returned."""
    for c in CASES:
        p = index.search(c["q"], c["region"], c["today"])
        if p is not None:
            assert p.region == c["region"] and p.in_force(c["today"]), (c, p.ref)


def test_default_search_quality_does_not_regress():
    """The eval as a regression test: the default (keyword) scored 21/24 when it was chosen."""
    r = evaluate(KeywordPolicyIndex(POLICIES), CASES, POLICIES)
    assert r["hits"] >= 21, r["misses"]


def test_default_beats_the_offline_vector_options():
    """Why keyword is the default. If this ever flips, revisit the choice (and this test)."""
    kw = evaluate(KeywordPolicyIndex(POLICIES), CASES, POLICIES)["hits"]
    vec = evaluate(InMemoryPolicyIndex(POLICIES, make_embedder("hashing")), CASES, POLICIES)["hits"]
    hyb = evaluate(HybridPolicyIndex(POLICIES, make_embedder("hashing")), CASES, POLICIES)["hits"]
    assert kw > hyb > vec


@pytest.mark.skipif(not os.getenv("CASEGUARD_TEST_PG"), reason="set CASEGUARD_TEST_PG to a PostgreSQL URL")
def test_pgvector_gives_the_same_answers_as_in_memory_vectors():
    """The SQL path (filter in WHERE, rank with <=>) must agree with the reference implementation."""
    emb = make_embedder("hashing")
    pg = PgVectorPolicyIndex(os.environ["CASEGUARD_TEST_PG"], emb)
    pg.index(POLICIES)
    mem = InMemoryPolicyIndex(POLICIES, emb)
    for c in CASES:
        a, b = pg.search(c["q"], c["region"], c["today"]), mem.search(c["q"], c["region"], c["today"])
        assert (a.ref if a else None) == (b.ref if b else None), c


def test_a_non_escalation_policy_is_never_used_for_eligibility(api, today):
    """If retrieval returns e.g. the returns policy, code refuses to decide with it."""
    import uuid
    from caseguard_agent.case_api import CaseApiClient
    from caseguard_agent.graph import AgentDeps, build_graph
    from caseguard_agent.llm import ScriptedLLM
    from caseguard_agent.runner import AgentRunner
    from .conftest import ASHA

    class AlwaysReturns:
        def search(self, query, region, today):
            return next(p for p in POLICIES if p.ref == "RET-1@v1")

    deps = AgentDeps(llm=ScriptedLLM(), policies=POLICIES, policy_index=AlwaysReturns(), today=lambda: today,
                     case_api=lambda t, c: CaseApiClient("http://case-api", t, c, transport=api.transport()),
                     retry_delays=(0, 0), poll_delays=(0, 0, 0, 0), sleep=lambda s: None)
    r = AgentRunner(build_graph(deps)).turn(str(uuid.uuid4()), ASHA, "Order 1042 is late, please escalate it.")
    assert [c["type"] for c in r["reply"]["claims"]] == ["no_applicable_policy"]
    assert api.posts == []
