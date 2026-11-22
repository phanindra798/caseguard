"""Load a scenario file and run it against the real stack.

A scenario (YAML) has:
  setup     actions before the conversation (stop the worker, arm a fail point, add proxy faults)
  turns     what the customer says (`say`) or what a supervisor decides (`supervisor`), actions
            between turns, and per-turn expectations
  recovery  actions after the conversation (restart a crashed service) + a condition to wait for
  final     database / Kafka / log checks once the system has settled
  expect_harness_failures   for "test the tester" scenarios: the codes the checkers MUST report
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from confluent_kafka import Consumer, TopicPartition

from caseguard_agent.case_api import CaseApiClient, SupervisorClient
from caseguard_agent.checkpoints import make_checkpointer
from caseguard_agent.graph import AgentDeps, build_graph
from caseguard_agent.llm import ScriptedLLM
from caseguard_agent.llm.doubles import GullibleLLM
from caseguard_agent.policies import load_policies
from caseguard_agent.runner import AgentRunner, Session

from . import checks
from .fault_proxy import Fault, FaultProxy
from .trace_collector import TraceCollector
from .stack import Stack

SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"
SESSIONS = {
    "asha": Session(tenant_id="acme", customer_id="cust-A1", region="US"),
    "ben": Session(tenant_id="acme", customer_id="cust-A2", region="EU"),
}
CLIENT_TIMEOUT_S = 1.0          # the agent gives up on a response after 1 s (the proxy holds 2.5 s)


@dataclass
class ScenarioResult:
    id: str
    title: str
    passed: bool
    skipped: str | None = None
    failures: list[str] = field(default_factory=list)
    caught: list[str] = field(default_factory=list)      # for expect_harness_failures scenarios
    turns: list[dict] = field(default_factory=list)
    injected: list[dict] = field(default_factory=list)
    seconds: float = 0.0


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[dict]:
    return [yaml.safe_load(p.read_text()) | {"_file": p.name} for p in sorted(directory.glob("*.yaml"))]


class ScenarioRunner:
    def __init__(self, stack: Stack, proxy: FaultProxy, collector: TraceCollector | None = None):
        self.stack, self.proxy, self.collector = stack, proxy, collector
        self.policies = load_policies()
        self.agent: AgentRunner | None = None
        self._llm_mode = "scripted"
        self._checkpointer = None

    # ------------------------------------------------------------------ agent
    def _agent(self, llm_mode: str, output_guard: bool = True) -> AgentRunner:
        """A fresh agent PROCESS: new graph, new connection pool, conversations only in PostgreSQL."""
        if self._checkpointer is not None:
            self._checkpointer.conn.close()
        self._checkpointer = make_checkpointer(self.stack.cfg.checkpoint_url)
        self._llm_mode, self._output_guard = llm_mode, output_guard
        llm = {"scripted": lambda: ScriptedLLM(), "overclaim": lambda: ScriptedLLM(overclaim=True),
               "gullible": lambda: GullibleLLM()}[llm_mode]()
        deps = AgentDeps(
            llm=llm,
            case_api=lambda t, c: CaseApiClient(self.proxy.url, t, c, timeout_s=CLIENT_TIMEOUT_S),
            policies=self.policies,
            retry_delays=(0.2, 0.4),
            poll_delays=(0.5, 1, 1.5, 2, 3),
            output_guard=output_guard)
        return AgentRunner(build_graph(deps, checkpointer=self._checkpointer))

    # ------------------------------------------------------------------ actions
    def _act(self, action) -> None:
        name, arg = (action, None) if isinstance(action, str) else next(iter(action.items()))
        if name == "stop_worker":
            self.stack.stop_worker()
        elif name == "start_worker":
            self.stack.start_worker((arg or {}).get("fail_point"))
        elif name == "wait_worker_crash":
            code = self.stack.wait_worker_exit(timeout=60)
            assert code != 0, "worker was expected to crash"
        elif name == "restart_api":
            self.stack.start_api((arg or {}).get("fail_point"))
        elif name == "wait_api_crash":
            code = self.stack.wait_api_exit(timeout=60)
            assert code != 0, "case-api was expected to crash"
        elif name == "restart_agent":
            self.agent = self._agent(self._llm_mode, self._output_guard)   # the old agent is simply dropped
        elif name == "sql":
            self.stack.execute(arg)
        elif name == "fault":
            self.proxy.add(Fault(**arg))
        elif name == "sleep":
            time.sleep(float(arg))
        else:
            raise ValueError(f"unknown action {name}")

    # ------------------------------------------------------------------ supervisor
    def _supervisor_decides(self, session: Session, conversation: str, decision: str) -> dict:
        """A supervisor of the same tenant decides the newest pending approval, then the paused
        conversation is resumed, exactly what the agent's /supervisor page does."""
        sup = SupervisorClient(self.proxy.url, session.tenant_id, "sup-harness")
        try:
            pending = sup.pending()
            assert pending, "no approval is pending"
            approval = sup.decide(pending[-1]["id"], decision)
        finally:
            sup.close()
        assert approval["conversationRef"] == conversation, "approval points at another conversation"
        return self.agent.resume(conversation, {"approval_id": approval["id"]})

    # ------------------------------------------------------------------ kafka
    def _deliveries_by_key(self) -> dict[str, int]:
        c = Consumer({"bootstrap.servers": self.stack.cfg.kafka, "group.id": f"harness-reader-{uuid.uuid4()}",
                      "enable.auto.commit": False})
        try:
            meta = c.list_topics(self.stack.cfg.topic, timeout=10)
            parts = [TopicPartition(self.stack.cfg.topic, p, 0) for p in meta.topics[self.stack.cfg.topic].partitions]
            highs = {p.partition: c.get_watermark_offsets(p, timeout=10)[1] for p in parts}
            c.assign(parts)
            counts: dict[str, int] = {}
            remaining = sum(highs.values())
            deadline = time.time() + 15
            while remaining > 0 and time.time() < deadline:
                m = c.poll(0.5)
                if m is None or m.error():
                    continue
                remaining -= 1
                k = m.key().decode() if m.key() else ""
                counts[k] = counts.get(k, 0) + 1
            return counts
        finally:
            c.close()

    # ------------------------------------------------------------------ run
    def run(self, sc: dict) -> ScenarioResult:
        started = time.time()
        result = ScenarioResult(id=sc["id"], title=sc["title"], passed=False)
        if sc.get("requires") == "tracing" and self.collector is None:
            result.passed, result.skipped = True, "tracing off (run scripts/fetch-otel-agent.sh, then --tracing)"
            return result
        failures: list[str] = []
        if self.collector is not None:
            self.collector.clear()
        self.proxy.clear()
        self.stack.reset()
        if self.stack.worker is None or self.stack.worker.poll() is not None:
            self.stack.start_worker()
        try:
            for a in sc.get("setup", []):
                self._act(a)
            self.agent = self._agent(sc.get("llm", "scripted"), sc.get("output_guard", True))
            session = SESSIONS[sc.get("session", "asha")]
            conversation = str(uuid.uuid4())

            for i, t in enumerate(sc["turns"], 1):
                for a in t.get("before", []):
                    self._act(a)
                if "supervisor" in t:
                    r = self._supervisor_decides(session, conversation, t["supervisor"]["decision"])
                    said = f"[supervisor] {t['supervisor']['decision']}"
                else:
                    r = self.agent.turn(conversation, session, t["say"])
                    said = t["say"]
                result.turns.append({"say": said, "reply": r["reply"]["text"],
                                     "claims": [c["type"] for c in r["reply"]["claims"]],
                                     "decisions": [f"{d['action']} {d.get('tool') or ''}".strip() for d in r["decisions"]],
                                     "tools": [f"{c['method']} {c['path']} -> {c['status'] or c['outcome']}"
                                               for c in r["tool_calls"]]})
                exp = t.get("expect", {})
                prefix = f"turn {i}: "
                failures += [prefix + e for e in checks.check_decisions(exp.get("decisions", {}), r["decisions"])]
                failures += [prefix + e for e in checks.check_tools(exp.get("tools", {}), r["tool_calls"])]
                failures += [prefix + e for e in checks.check_claims(exp.get("claims", {}), r["reply"], r["all_evidence"])]
                if "waiting" in exp and bool(r.get("waiting_for_approval")) != exp["waiting"]:
                    failures.append(prefix + f"WAITING_STATE: expected waiting={exp['waiting']}, "
                                             f"got {r.get('waiting_for_approval')!r}")
                for a in t.get("after", []):
                    self._act(a)

            rec = sc.get("recovery", {})
            for a in rec.get("actions", []):
                self._act(a)
            if "wait_until" in rec and not self.stack.wait_until(rec["wait_until"], rec.get("timeout", 60)):
                failures.append(f"RECOVERY_TIMEOUT: `{rec['wait_until']}` never became true")
            if "wait_for_worker_log" in rec:
                deadline = time.time() + rec.get("timeout", 60)
                while rec["wait_for_worker_log"] not in self.stack.worker_logs() and time.time() < deadline:
                    time.sleep(0.5)
                if rec["wait_for_worker_log"] not in self.stack.worker_logs():
                    failures.append(f"RECOVERY_TIMEOUT: worker never logged '{rec['wait_for_worker_log']}'")

            final = sc.get("final", {})
            failures += checks.check_db(final.get("db", []), self.stack.sql)
            if "events" in final:
                escalations = self.stack.sql("select coalesce(array_agg(id::text), '{}') from escalations")
                failures += checks.check_events(final["events"], self._deliveries_by_key(), list(escalations))
            if "worker_log" in final:
                failures += checks.check_logs(final["worker_log"], self.stack.worker_logs())
            if "trace" in final:
                from caseguard_agent.tracing import flush
                deadline = time.time() + 20
                while True:                                   # spans arrive in batches: give them a moment
                    flush()
                    errs = checks.check_trace(final["trace"], self.collector.traces())
                    if not errs or time.time() > deadline:
                        break
                    time.sleep(1)
                failures += errs
        except Exception as e:      # an exploding scenario is a failure, not a harness crash
            failures.append(f"HARNESS_ERROR: {type(e).__name__}: {e}")

        expected = sc.get("expect_harness_failures")
        if expected:
            # "Test the tester": pass only if the checkers caught exactly these problems.
            result.caught = sorted({f.split(": ", 2)[1].split(":")[0] for f in failures if ": " in f})
            missing = [code for code in expected if not any(code in f for f in failures)]
            result.passed = not missing
            result.failures = [f"HARNESS_MISSED: checkers should have reported {missing}"] if missing else []
        else:
            result.passed = not failures
            result.failures = failures
        result.injected = list(self.proxy.injected)
        result.seconds = round(time.time() - started, 1)
        # Leave the stack healthy for the next scenario.
        if self.stack.api is None or self.stack.api.poll() is not None:
            self.stack.start_api()
        return result
