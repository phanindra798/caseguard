"""Measure policy retrieval on labelled customer queries (evals/retrieval.yaml).

    python -m caseguard_agent.retrieval_eval                       # keyword baseline + hashing
    python -m caseguard_agent.retrieval_eval --embedder fastembed  # + a semantic model
    CASEGUARD_POLICY_DB=postgresql://... python -m caseguard_agent.retrieval_eval   # + pgvector

Reports accuracy@1 overall and per policy kind, plus every miss, so a change of embedder or policy
wording is judged by numbers, not by feel.
"""
from __future__ import annotations

import argparse
import os
from collections import defaultdict
from pathlib import Path

import yaml

from .embeddings import make_embedder
from .policies import (HybridPolicyIndex, InMemoryPolicyIndex, KeywordPolicyIndex, PgVectorPolicyIndex,
                       load_policies)

CASES = Path(__file__).resolve().parents[2] / "evals" / "retrieval.yaml"


def load_cases(path: Path = CASES) -> list[dict]:
    return yaml.safe_load(path.read_text())


def evaluate(index, cases: list[dict], policies) -> dict:
    kind_of = {p.ref: p.kind for p in policies}
    hits, per_kind, misses = 0, defaultdict(lambda: [0, 0]), []
    for c in cases:
        got = index.search(c["q"], c["region"], c["today"])
        got_ref = got.ref if got else None
        ok = got_ref == c["expect"]
        hits += ok
        k = kind_of.get(c["expect"], "none")
        per_kind[k][0] += ok
        per_kind[k][1] += 1
        if not ok:
            misses.append({"q": c["q"], "region": c["region"], "expected": c["expect"], "got": got_ref})
    return {"index": index.name, "accuracy": hits / len(cases), "hits": hits, "total": len(cases),
            "per_kind": {k: f"{a}/{b}" for k, (a, b) in sorted(per_kind.items())}, "misses": misses}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--embedder", action="append", default=[], help="extra embedders, e.g. fastembed")
    args = ap.parse_args()
    policies, cases = load_policies(), load_cases()
    indexes = [KeywordPolicyIndex(policies), InMemoryPolicyIndex(policies, make_embedder("hashing")),
               HybridPolicyIndex(policies, make_embedder("hashing"))]
    for e in args.embedder:
        indexes += [InMemoryPolicyIndex(policies, make_embedder(e)), HybridPolicyIndex(policies, make_embedder(e))]
    if os.getenv("CASEGUARD_POLICY_DB"):
        pg = PgVectorPolicyIndex(os.environ["CASEGUARD_POLICY_DB"], make_embedder("hashing"))
        pg.index(policies)
        indexes.append(pg)
    print(f"{len(cases)} labelled queries, {len(policies)} policies\n")
    print("| index | accuracy@1 | per kind |\n|---|---|---|")
    results = [evaluate(i, cases, policies) for i in indexes]
    for r in results:
        print(f"| {r['index']} | {r['hits']}/{r['total']} ({r['accuracy']:.0%}) | {r['per_kind']} |")
    for r in results:
        if r["misses"]:
            print(f"\nmisses for {r['index']}:")
            for m in r["misses"]:
                print(f"  - [{m['region']}] {m['q']!r}: expected {m['expected']}, got {m['got']}")


if __name__ == "__main__":
    main()
