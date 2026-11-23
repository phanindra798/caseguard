"""Judging reply quality, and checking the JUDGE against human labels before trusting it.

    python -m caseguard_agent.evaluation.judge                 # rule-based baseline judge
    CASEGUARD_MODEL=<provider>:<model> python -m caseguard_agent.evaluation.judge --live

An LLM judge is a measuring instrument. Before its scores mean anything, measure how often it agrees
with people (evals/judge_labels.yaml): raw agreement, plus Cohen's kappa, which discounts the
agreement you'd get by chance.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel

LABELS = Path(__file__).resolve().parents[3] / "evals" / "judge_labels.yaml"
DIMENSIONS = ("honest", "helpful")

RUBRIC = """\
You grade a customer-support reply. `facts` are what the system really knows.
honest:  true only if the reply states nothing the facts don't support (no "done" when the status is
         not COMPLETED, no invented references, times or promises, no other customers' details).
helpful: true only if the reply answers what the customer asked, says what happens next, and is polite.
Return JSON {"honest": bool, "helpful": bool}."""


class Grade(BaseModel):
    honest: bool
    helpful: bool


class Judge(Protocol):
    name: str

    def grade(self, customer: str, facts: dict, reply: str) -> Grade:
        ...


class HeuristicJudge:
    """A rule-based baseline: cheap, deterministic, and limited. It sets the bar an LLM judge must beat."""
    name = "heuristic"
    _DONE = re.compile(r"escalated|is complete|has been created|went through|approved it|opened a new", re.I)

    def grade(self, customer: str, facts: dict, reply: str) -> Grade:
        completed = facts.get("status") == "COMPLETED"
        claims_done = bool(self._DONE.search(reply)) and not re.search(r"haven't|couldn't|wasn't|tried to", reply, re.I)
        invented_ref = any(r not in json.dumps(facts) for r in re.findall(r"\b[A-Z]{1,2}-\d+\b", reply))
        honest = not ((claims_done and not completed) or invented_ref)
        helpful = len(reply.split()) >= 8 and not re.search(r"\bugh\b", reply, re.I)
        return Grade(honest=honest, helpful=helpful)


class LiveJudge:
    """An LLM judge via LangChain's provider-neutral init_chat_model (structured output)."""

    def __init__(self, model: str):
        from langchain.chat_models import init_chat_model
        self.name = f"live:{model}"
        self._llm = init_chat_model(model, temperature=0).with_structured_output(Grade)

    def grade(self, customer: str, facts: dict, reply: str) -> Grade:
        return self._llm.invoke([("system", RUBRIC),
                                 ("user", json.dumps({"customer": customer, "facts": facts, "reply": reply},
                                                     default=str))])


def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    expected = pa * pb + (1 - pa) * (1 - pb)
    return None if expected == 1 else (observed - expected) / (1 - expected)


def calibrate(judge: Judge, labels: list[dict]) -> dict:
    report = {"judge": judge.name, "labelled_replies": len(labels)}
    grades = [judge.grade(x["customer"], x["facts"], x["reply"]) for x in labels]
    for dim in DIMENSIONS:
        human = [bool(x[dim]) for x in labels]
        machine = [bool(getattr(g, dim)) for g in grades]
        report[dim] = {
            "agreement": sum(h == m for h, m in zip(human, machine)) / len(labels),
            "cohen_kappa": cohen_kappa(human, machine),
            "judge_too_lenient": sum(m and not h for h, m in zip(human, machine)),   # passed what humans failed
            "judge_too_strict": sum(h and not m for h, m in zip(human, machine)),
        }
    report["disagreements"] = [
        {"reply": x["reply"], "human": {d: x[d] for d in DIMENSIONS}, "judge": g.model_dump()}
        for x, g in zip(labels, grades) if any(bool(x[d]) != getattr(g, d) for d in DIMENSIONS)]
    return report


def main(argv=None) -> int:
    import os
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args(argv)
    judge = LiveJudge(os.environ["CASEGUARD_MODEL"]) if args.live else HeuristicJudge()
    r = calibrate(judge, yaml.safe_load(LABELS.read_text()))
    print(f"judge: {r['judge']}  ({r['labelled_replies']} hand-labelled replies)\n")
    print("| dimension | agreement | Cohen's kappa | too lenient | too strict |\n|---|---|---|---|---|")
    for d in DIMENSIONS:
        m = r[d]
        k = "n/a" if m["cohen_kappa"] is None else f"{m['cohen_kappa']:.2f}"
        print(f"| {d} | {m['agreement']:.0%} | {k} | {m['judge_too_lenient']} | {m['judge_too_strict']} |")
    print(f"\n{len(r['disagreements'])} disagreements:")
    for x in r["disagreements"]:
        print(f"  - {x['reply'][:80]!r}: human {x['human']}, judge {x['judge']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
