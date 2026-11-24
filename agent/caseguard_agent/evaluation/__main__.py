"""Evaluate one or more agent configurations on the labelled conversations.

    python -m caseguard_agent.evaluation                        # the standard comparison
    python -m caseguard_agent.evaluation --configs scripted --trials 3 --report eval.md
    CASEGUARD_MODEL=<provider>:<model> python -m caseguard_agent.evaluation --configs live --trials 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .metrics import compute, fmt
from .runner import Config, load_cases, run_eval

CONFIGS = {
    "scripted": Config("scripted (guard on)", "scripted", True),
    "gullible": Config("gullible model (guard on)", "gullible", True),
    "gullible-noguard": Config("gullible model (guard OFF)", "gullible", False),
    "overclaim": Config("over-claiming model (guard on)", "overclaim", True),
    "overclaim-noguard": Config("over-claiming model (guard OFF)", "overclaim", False),
}
ROWS = [("pass rate", "pass_rate"), ("action accuracy", "action_accuracy"),
        ("false-success rate (of completion claims)", "false_success_rate"),
        ("text over-claims (of replies)", "text_overclaims"),
        ("unsafe writes (of no-write cases)", "unsafe_write_rate"),
        ("citation accuracy", "citation_accuracy"), ("missed questions", "missed_question_rate"),
        ("unnecessary questions", "unnecessary_question_rate"), ("attacks blocked", "attack_block_rate"),
        ("false blocks (of legitimate cases)", "false_block_rate")]


def render(named: list[tuple[str, dict]], n_cases: int, trials: int) -> str:
    out = [f"{n_cases} labelled conversations × {trials} trial(s) per configuration", "",
           "| metric | " + " | ".join(n for n, _ in named) + " |", "|---|" + "---|" * len(named)]
    for label, key in ROWS:
        out.append(f"| {label} | " + " | ".join(fmt(m[key]) for _, m in named) + " |")
    out.append("| tool calls per conversation | " + " | ".join(str(m["tool_calls_per_conversation"]) for _, m in named) + " |")
    out.append("| turn latency p50 / p95 (ms) | " + " | ".join(
        f"{m['turn_latency_ms']['p50']} / {m['turn_latency_ms']['p95']}" for _, m in named) + " |")
    out.append("| LLM calls per conversation | " + " | ".join(str(m["llm_calls_per_conversation"]) for _, m in named) + " |")
    out.append("| tokens per conversation (in / out, p95 total) | " + " | ".join(
        f"{m['tokens_per_conversation']['mean_input']} / {m['tokens_per_conversation']['mean_output']}, "
        f"p95 {m['tokens_per_conversation']['p95_total']}{' (estimated)' if m['tokens_per_conversation']['estimated'] else ''}"
        for _, m in named) + " |")
    out.append("| cost per conversation (USD) | " + " | ".join(
        "set CASEGUARD_PRICE_*" if m["tokens_per_conversation"]["cost_per_conversation_usd"] is None
        else f"{m['tokens_per_conversation']['cost_per_conversation_usd']:.5f}" for _, m in named) + " |")
    out.append("| pass-rate stdev across trials | " + " | ".join(str(m["pass_rate_stdev_across_trials"]) for _, m in named) + " |")
    for name, m in named:
        if m["errors"]:
            out += ["", f"**Error taxonomy, {name}:** " + ", ".join(f"`{k}` × {v}" for k, v in m["errors"].items())]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="scripted,gullible,gullible-noguard,overclaim,overclaim-noguard")
    ap.add_argument("--trials", type=int, default=1)
    ap.add_argument("--split", default="all", choices=["dev", "holdout", "all"])
    ap.add_argument("--report", type=Path)
    ap.add_argument("--failures", action="store_true", help="list every failed run")
    args = ap.parse_args(argv)
    if "live" in args.configs:
        CONFIGS["live"] = Config(f"live: {os.getenv('CASEGUARD_MODEL')}", "live", True, os.getenv("CASEGUARD_MODEL"))
    cases = load_cases(split=args.split)
    named, all_results = [], {}
    for key in args.configs.split(","):
        cfg = CONFIGS[key]
        results = run_eval(cfg, cases, args.trials)
        all_results[key] = results
        named.append((cfg.name, compute(results)))
    text = f"split: {args.split}\n" + render(named, len(cases), args.trials)
    print(text)
    if args.failures:
        for key, results in all_results.items():
            for r in results:
                if r.errors:
                    print(f"  [{key}] {r.case_id} {r.category}: {r.errors} | said: {r.reply[:90]!r}")
    if args.report:
        args.report.write_text(text + "\n")
        args.report.with_suffix(".json").write_text(json.dumps({n: m for n, m in named}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
