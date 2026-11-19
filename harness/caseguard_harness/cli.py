"""Run the scenario suite against the real stack and write a report.

    python -m caseguard_harness                    # all scenarios
    python -m caseguard_harness -k S04 -k S19      # only these
    python -m caseguard_harness --report report.md

Exit code 0 only if every scenario passed, so CI can block a merge on it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .fault_proxy import FaultProxy
from .scenario import ScenarioRunner, load_scenarios
from .stack import Stack


def render_report(results) -> str:
    passed = sum(r.passed for r in results)
    lines = ["# CaseGuard harness report", "",
             f"**{passed}/{len(results)} scenarios passed**", "",
             "| Scenario | Result | Time | Notes |", "|---|---|---|---|"]
    for r in results:
        note = "; ".join(r.failures) if r.failures else (f"caught: {', '.join(r.caught)}" if r.caught else "")
        lines.append(f"| {r.id} {r.title} | {'✅ pass' if r.passed else '❌ FAIL'} | {r.seconds}s | {note} |")
    lines.append("")
    for r in results:
        lines += [f"## {r.id} {r.title}", ""]
        if r.injected:
            lines.append("Faults injected: " + ", ".join(f"{f['mode']} on {f['method']} {f['path']}" for f in r.injected))
            lines.append("")
        for i, t in enumerate(r.turns, 1):
            lines += [f"**Turn {i}. Customer:** {t['say']}", "",
                      "- decisions: " + " → ".join(t["decisions"]),
                      "- tool calls: " + "; ".join(t["tools"]),
                      f"- claims: {t['claims']}",
                      f"- reply: {t['reply']}", ""]
        for f in r.failures:
            lines.append(f"- ❌ {f}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", action="append", default=[], help="run only scenarios whose id starts with this")
    ap.add_argument("--report", type=Path, default=Path("harness-report.md"))
    args = ap.parse_args(argv)

    scenarios = [s for s in load_scenarios() if not args.k or any(s["id"].startswith(k) for k in args.k)]
    stack = Stack()
    print(f"starting stack (db {stack.cfg.database}, topic {stack.cfg.topic}, logs {stack.log_dir})", flush=True)
    stack.start()
    proxy = FaultProxy(stack.api_url).start()
    results = []
    try:
        runner = ScenarioRunner(stack, proxy)
        for sc in scenarios:
            r = runner.run(sc)
            results.append(r)
            mark = "PASS" if r.passed else "FAIL"
            print(f"  {mark}  {r.id:<4} {r.title} ({r.seconds}s)", flush=True)
            for f in r.failures:
                print(f"        - {f}", flush=True)
    finally:
        proxy.stop()
        stack.stop()

    args.report.write_text(render_report(results))
    args.report.with_suffix(".json").write_text(json.dumps([r.__dict__ for r in results], indent=2, default=str))
    passed = sum(r.passed for r in results)
    print(f"\n{passed}/{len(results)} scenarios passed. Report: {args.report}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
