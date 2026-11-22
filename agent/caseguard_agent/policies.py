"""Versioned policy documents, and how the agent finds the one that applies.

Rule: FILTER by metadata first (region, effective dates), THEN rank by similarity.
Similarity alone could return an expired or wrong-region policy that merely "sounds right".

Interchangeable indexes, all with the same filter-then-rank rule:
  KeywordPolicyIndex   shared words                                         ← DEFAULT (best measured offline)
  InMemoryPolicyIndex  vector similarity, vectors in process memory
  HybridPolicyIndex    keyword + vector rankings fused (reciprocal rank fusion)
  PgVectorPolicyIndex  vector similarity in PostgreSQL; filtering AND ranking in one SQL query

Why keyword is the default: on the labelled queries in evals/retrieval.yaml, keyword search scored
21/24, while the offline hashing vectors scored 16/24 and hybrid 18/24. Hashing vectors are lexical,
not semantic. Vector search is meant for a real semantic embedder (CASEGUARD_EMBEDDER=fastembed),
measured on the same queries by `python -m caseguard_agent.retrieval_eval --embedder fastembed`.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

import yaml

from .embeddings import Embedder, cosine, make_embedder

DEFAULT_DIR = Path(os.getenv("CASEGUARD_POLICIES_DIR", Path(__file__).resolve().parents[2] / "policies"))
ESCALATION_KIND = "late_delivery_escalation"


@dataclass(frozen=True)
class Policy:
    id: str
    version: int
    title: str
    kind: str
    region: str
    effective_from: date
    effective_to: date | None       # exclusive; None = still in force
    min_business_days_late: int | None
    approval_over_cents: int | None
    text: str

    @property
    def ref(self) -> str:
        """Citation format used everywhere, e.g. 'ESC-3@v2'."""
        return f"{self.id}@v{self.version}"

    @property
    def search_text(self) -> str:
        return f"{self.title}. {self.text}"

    def in_force(self, today: date) -> bool:
        return self.effective_from <= today and (self.effective_to is None or today < self.effective_to)

    def summary(self) -> dict:
        return {"ref": self.ref, "title": self.title, "kind": self.kind, "region": self.region,
                "min_business_days_late": self.min_business_days_late,
                "approval_over_cents": self.approval_over_cents}


def _parse(path: Path) -> Policy:
    _, front, body = path.read_text(encoding="utf-8").split("---", 2)
    m = yaml.safe_load(front)
    opt_int = lambda k: int(m[k]) if m.get(k) is not None else None
    return Policy(id=m["id"], version=int(m["version"]), title=m["title"], kind=m["kind"], region=m["region"],
                  effective_from=m["effective_from"], effective_to=m.get("effective_to"),
                  min_business_days_late=opt_int("min_business_days_late"),
                  approval_over_cents=opt_int("approval_over_cents"), text=body.strip())


def load_policies(directory: Path = DEFAULT_DIR) -> list[Policy]:
    return [_parse(p) for p in sorted(directory.glob("*.md"))]


class PolicyIndex(Protocol):
    def search(self, query: str, region: str, today: date) -> Policy | None:
        ...


# ------------------------------------------------------------------ keyword baseline (for the eval)
def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower()))


class KeywordPolicyIndex:
    """The Phase 1.4 approach: count shared words. Kept as the baseline the eval compares against."""
    name = "keyword"

    def __init__(self, policies: list[Policy]):
        self.policies = policies

    def search(self, query: str, region: str, today: date) -> Policy | None:
        candidates = [p for p in self.policies if p.region == region and p.in_force(today)]
        if not candidates:
            return None
        q = _words(query)
        return max(candidates, key=lambda p: (len(q & _words(p.search_text)), p.version))


# ------------------------------------------------------------------ vector search, in memory
class InMemoryPolicyIndex:
    def __init__(self, policies: list[Policy], embedder: Embedder | None = None):
        self.policies = policies
        self.embedder = embedder or make_embedder()
        self.name = self.embedder.name
        self._vectors = dict(zip((p.ref for p in policies), self.embedder.embed([p.search_text for p in policies])))

    def search(self, query: str, region: str, today: date) -> Policy | None:
        candidates = [p for p in self.policies if p.region == region and p.in_force(today)]   # 1. filter
        if not candidates:
            return None
        q = self.embedder.embed([query])[0]
        return max(candidates, key=lambda p: cosine(q, self._vectors[p.ref]))               # 2. rank


# ------------------------------------------------------------------ hybrid: keyword + vector
class HybridPolicyIndex:
    """Reciprocal rank fusion of keyword ranking and vector ranking: score = sum 1/(k + rank).
    Each method rescues the other's misses; neither score scale has to be calibrated."""

    def __init__(self, policies: list[Policy], embedder: Embedder | None = None, k: int = 60):
        self.policies, self.k = policies, k
        self._vec = InMemoryPolicyIndex(policies, embedder)
        self.name = f"hybrid/{self._vec.name}"

    def search(self, query: str, region: str, today: date) -> Policy | None:
        candidates = [p for p in self.policies if p.region == region and p.in_force(today)]   # 1. filter
        if not candidates:
            return None
        q_words, q_vec = _words(query), self._vec.embedder.embed([query])[0]
        by_kw = sorted(candidates, key=lambda p: -len(q_words & _words(p.search_text)))
        by_vec = sorted(candidates, key=lambda p: -cosine(q_vec, self._vec._vectors[p.ref]))
        score = {p.ref: 1 / (self.k + by_kw.index(p) + 1) + 1 / (self.k + by_vec.index(p) + 1)
                 for p in candidates}
        return max(candidates, key=lambda p: (score[p.ref], p.version))                     # 2. fuse


# ------------------------------------------------------------------ vector search, PostgreSQL + pgvector
class PgVectorPolicyIndex:
    """Policies and their vectors in PostgreSQL. One table per embedder (dimensions must match)."""

    def __init__(self, url: str, embedder: Embedder | None = None):
        import psycopg

        self.embedder = embedder or make_embedder()
        self.name = f"pgvector/{self.embedder.name}"
        self.table = f"policy_index_{self.embedder.name}"
        self._conn = psycopg.connect(url, autocommit=True)
        self._conn.execute("create extension if not exists vector")
        self._conn.execute(f"""
            create table if not exists {self.table} (
              ref            text primary key,
              id             text not null,
              version        int  not null,
              title          text not null,
              kind           text not null,
              region         text not null,
              effective_from date not null,
              effective_to   date,
              min_days       int,
              approval_over  bigint,
              body           text not null,
              embedding      vector({self.embedder.dim}) not null)""")

    def index(self, policies: list[Policy]) -> int:
        """Embed and upsert every policy (idempotent: re-running updates in place)."""
        vectors = self.embedder.embed([p.search_text for p in policies])
        with self._conn.transaction():
            for p, v in zip(policies, vectors):
                self._conn.execute(f"""
                    insert into {self.table} values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)
                    on conflict (ref) do update set title = excluded.title, kind = excluded.kind,
                      region = excluded.region, effective_from = excluded.effective_from,
                      effective_to = excluded.effective_to, min_days = excluded.min_days,
                      approval_over = excluded.approval_over, body = excluded.body,
                      embedding = excluded.embedding""",
                    (p.ref, p.id, p.version, p.title, p.kind, p.region, p.effective_from, p.effective_to,
                     p.min_business_days_late, p.approval_over_cents, p.text, _literal(v)))
        return len(policies)

    def search(self, query: str, region: str, today: date) -> Policy | None:
        q = _literal(self.embedder.embed([query])[0])
        # Filter in the WHERE clause, rank with the cosine-distance operator <=>, in ONE query.
        row = self._conn.execute(f"""
            select id, version, title, kind, region, effective_from, effective_to, min_days, approval_over, body
              from {self.table}
             where region = %s and effective_from <= %s and (effective_to is null or %s < effective_to)
             order by embedding <=> %s::vector
             limit 1""", (region, today, today, q)).fetchone()
        if row is None:
            return None
        return Policy(id=row[0], version=row[1], title=row[2], kind=row[3], region=row[4], effective_from=row[5],
                      effective_to=row[6], min_business_days_late=row[7], approval_over_cents=row[8], text=row[9])


def _literal(v: list[float]) -> str:
    """pgvector's text format: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def make_policy_index(policies: list[Policy] | None = None) -> PolicyIndex:
    """CASEGUARD_POLICY_SEARCH = keyword (default) | vector | hybrid.
    With `vector` and CASEGUARD_POLICY_DB=postgresql://..., vectors live in pgvector (indexed on startup)."""
    policies = policies if policies is not None else load_policies()
    mode = os.getenv("CASEGUARD_POLICY_SEARCH", "keyword")
    if mode == "keyword":
        return KeywordPolicyIndex(policies)
    if mode == "hybrid":
        return HybridPolicyIndex(policies)
    if mode == "vector":
        url = os.getenv("CASEGUARD_POLICY_DB")
        if url:
            idx = PgVectorPolicyIndex(url)
            idx.index(policies)
            return idx
        return InMemoryPolicyIndex(policies)
    raise ValueError(f"unknown CASEGUARD_POLICY_SEARCH: {mode}")


def search_policy(policies: list[Policy], query: str, region: str, today: date) -> Policy | None:
    """Convenience for callers/tests: the default (keyword) search over `policies`."""
    return KeywordPolicyIndex(policies).search(query, region, today)
