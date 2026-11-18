"""Versioned policy documents and how the agent picks the one that applies.

Rule: FILTER by metadata first (region, effective dates), THEN rank by relevance.
Ranking alone could return an expired or wrong-region policy that merely "sounds right".
Phase 1.4 ranks by keyword overlap; Release 2 swaps in vector search (pgvector), same filter.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

DEFAULT_DIR = Path(os.getenv("CASEGUARD_POLICIES_DIR", Path(__file__).resolve().parents[2] / "policies"))


@dataclass(frozen=True)
class Policy:
    id: str
    version: int
    title: str
    region: str
    effective_from: date
    effective_to: date | None       # exclusive; None = still in force
    min_business_days_late: int
    approval_over_cents: int | None
    text: str

    @property
    def ref(self) -> str:
        """Citation format used everywhere, e.g. 'ESC-3@v2'."""
        return f"{self.id}@v{self.version}"

    def in_force(self, today: date) -> bool:
        return self.effective_from <= today and (self.effective_to is None or today < self.effective_to)

    def summary(self) -> dict:
        return {"ref": self.ref, "title": self.title, "region": self.region,
                "min_business_days_late": self.min_business_days_late,
                "approval_over_cents": self.approval_over_cents}


def _parse(path: Path) -> Policy:
    raw = path.read_text(encoding="utf-8")
    _, front, body = raw.split("---", 2)
    meta = yaml.safe_load(front)
    return Policy(
        id=meta["id"], version=int(meta["version"]), title=meta["title"], region=meta["region"],
        effective_from=meta["effective_from"], effective_to=meta.get("effective_to"),
        min_business_days_late=int(meta["min_business_days_late"]),
        approval_over_cents=meta.get("approval_over_cents"), text=body.strip())


def load_policies(directory: Path = DEFAULT_DIR) -> list[Policy]:
    return [_parse(p) for p in sorted(directory.glob("*.md"))]


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower()))


def search_policy(policies: list[Policy], query: str, region: str, today: date) -> Policy | None:
    candidates = [p for p in policies if p.region == region and p.in_force(today)]   # 1. filter
    if not candidates:
        return None
    q = _words(query)
    return max(candidates, key=lambda p: (len(q & _words(p.title + " " + p.text)), p.version))  # 2. rank
