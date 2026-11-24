"""Load test of the REAL escalation pipeline: Case API → PostgreSQL → outbox relay → Kafka → worker.

    python -m caseguard_harness.loadtest --escalations 2000 --concurrency 50 --duplicates 0.2

What it does:
  1. starts the real Case API and worker on a fresh database, seeds N open cases for a load-test customer;
  2. fires N escalation requests at the given concurrency, plus RETRIES (same idempotency key) for a
     share of them, plus reads, all shuffled together, so duplicates can race their originals;
  3. waits until the worker has completed every escalation;
  4. reports throughput, API latency percentiles, end-to-end lag (accepted → task created), and checks
     the invariants: exactly one escalation, one outbox event, one processed event and one task per case.

Everything shares one machine (database, broker, both services and this load generator), so the numbers
show relative behaviour and bottlenecks, not production capacity.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

import httpx
import psycopg

from .stack import Stack

TENANT, CUSTOMER = "acme", "cust-load"
HEADERS = {"X-Tenant-Id": TENANT, "X-Customer-Id": CUSTOMER, "Content-Type": "application/json"}


def seed(stack: Stack, n: int) -> list[str]:
    """N late, cheap orders with open cases (no approval needed), owned by one load-test customer."""
    with psycopg.connect(stack.cfg.dsn(), autocommit=True) as conn:
        conn.execute("insert into customers (id, tenant_id, name, region) values (%s, %s, 'Load Test', 'US')",
                     (CUSTOMER, TENANT))
        with conn.cursor() as cur:
            cur.executemany("""insert into orders (id, tenant_id, customer_id, description, amount_cents, status,
                                                   promised_date) values (%s, %s, %s, 'load item', 5000, 'SHIPPED',
                                                   current_date - 12)""",
                            [(f"L{i}", TENANT, CUSTOMER) for i in range(n)])
            cur.executemany("""insert into cases (id, tenant_id, customer_id, order_id, subject, status)
                               values (%s, %s, %s, %s, 'load', 'OPEN')""",
                            [(f"C-L{i}", TENANT, CUSTOMER, f"L{i}") for i in range(n)])
    return [f"C-L{i}" for i in range(n)]


async def fire(base: str, cases: list[str], concurrency: int, duplicates: float, reads: float, seed_: int):
    rng = random.Random(seed_)
    work = []
    for case_id in cases:
        key = str(uuid.uuid4())
        work.append(("escalate", case_id, key))
        if rng.random() < duplicates:
            work.append(("retry", case_id, key))         # same key: must replay, never duplicate
        if rng.random() < reads:
            work.append(("read", case_id, None))
    rng.shuffle(work)

    latencies: dict[str, list[float]] = defaultdict(list)
    statuses: dict[str, Counter] = defaultdict(Counter)
    sem = asyncio.Semaphore(concurrency)
    body = '{"reason":"load test: order is 12 days late","policyRef":"ESC-3@v2"}'
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(base_url=base, headers=HEADERS, limits=limits, timeout=30) as client:
        async def one(kind, case_id, key):
            async with sem:
                t0 = time.perf_counter()
                try:
                    if kind == "read":
                        r = await client.get(f"/cases/{case_id}")
                    else:
                        r = await client.post(f"/cases/{case_id}/escalations", content=body,
                                              headers={"Idempotency-Key": key})
                    status = r.status_code
                except httpx.HTTPError as e:
                    status = type(e).__name__
                latencies[kind].append((time.perf_counter() - t0) * 1000)
                statuses[kind][status] += 1

        started = time.perf_counter()
        await asyncio.gather(*(one(*w) for w in work))
        elapsed = time.perf_counter() - started
    return len(work), elapsed, latencies, statuses


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(p * len(s)))] if s else float("nan")


def wait_for_completion(stack: Stack, n: int, timeout: float) -> float:
    started = time.perf_counter()
    while time.perf_counter() - started < timeout:
        if stack.sql("select count(*) from escalations where status = 'COMPLETED'") >= n:
            return time.perf_counter() - started
        time.sleep(0.5)
    return float("nan")


def db_stats(stack: Stack) -> dict:
    with psycopg.connect(stack.cfg.dsn(), autocommit=True) as conn:
        one = lambda q: conn.execute(q).fetchone()[0]
        lags = [r[0] for r in conn.execute("""select extract(epoch from (completed_at - requested_at)) * 1000
                                                from escalations where completed_at is not null""")]
        publish = [r[0] for r in conn.execute("""select extract(epoch from (published_at - created_at)) * 1000
                                                   from outbox where published_at is not null""")]
        return {
            "escalations": one("select count(*) from escalations"),
            "distinct_cases": one("select count(distinct case_id) from escalations"),
            "completed": one("select count(*) from escalations where status = 'COMPLETED'"),
            "outbox_rows": one("select count(*) from outbox"),
            "outbox_unpublished": one("select count(*) from outbox where published_at is null"),
            "processed_events": one("select count(*) from processed_events"),
            "tasks": one("select count(*) from follow_up_tasks"),
            "lag_ms": [float(x) for x in lags], "publish_lag_ms": [float(x) for x in publish],
        }


def render(args, total, elapsed, lat, statuses, drain_s, db, cpus) -> str:
    n = args.escalations
    rows = [f"# Load test: {n} escalations, concurrency {args.concurrency}, "
            f"{args.duplicates:.0%} retries with the same key, {args.reads:.0%} reads",
            "", f"Machine: {cpus} CPUs shared by PostgreSQL, Kafka, Case API, worker and the load generator.", "",
            "| | |", "|---|---|",
            f"| HTTP requests | {total} in {elapsed:.1f} s → **{total / elapsed:.0f} req/s** |"]
    for kind in ("escalate", "retry", "read"):
        if lat.get(kind):
            rows.append(f"| {kind} latency p50 / p95 / p99 | {pct(lat[kind], .5):.0f} / {pct(lat[kind], .95):.0f} / "
                        f"{pct(lat[kind], .99):.0f} ms |")
    rows.append("| HTTP statuses | " + "; ".join(f"{k}: {dict(v)}" for k, v in statuses.items()) + " |")
    rows.append(f"| outbox publish lag p50 / p95 (row written → published) | {pct(db['publish_lag_ms'], .5):.0f} / "
                f"{pct(db['publish_lag_ms'], .95):.0f} ms |")
    rows.append(f"| end-to-end lag p50 / p95 / max (accepted → task created) | {pct(db['lag_ms'], .5):.0f} / "
                f"{pct(db['lag_ms'], .95):.0f} / {max(db['lag_ms'] or [float('nan')]):.0f} ms |")
    rows.append(f"| backlog drained after the last request | {drain_s:.1f} s |")
    checks = {
        "one escalation per case": db["escalations"] == n and db["distinct_cases"] == n,
        "every escalation completed": db["completed"] == n,
        "one outbox event per escalation, all published": db["outbox_rows"] == n and db["outbox_unpublished"] == 0,
        "one processed event per escalation": db["processed_events"] == n,
        "one follow-up task per escalation": db["tasks"] == n,
        "no server errors": not any(str(s).startswith("5") or not str(s).isdigit()
                                   for c in statuses.values() for s in c),
        "every retry replayed or won the race (200/202), never a duplicate": set(statuses.get("retry", {})) <= {200, 202},
    }
    rows += ["", "**Invariants under load**", ""] + [f"- {'✅' if ok else '❌'} {name}" for name, ok in checks.items()]
    rows += ["", f"Counts: {({k: v for k, v in db.items() if not k.endswith('_ms')})}"]
    return "\n".join(rows), all(checks.values())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--escalations", type=int, default=2000)
    ap.add_argument("--concurrency", type=int, default=50)
    ap.add_argument("--duplicates", type=float, default=0.2)
    ap.add_argument("--reads", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)

    stack = Stack()
    stack.cfg.database = "caseguard_load"
    print(f"starting stack (logs {stack.log_dir})", flush=True)
    stack.start()
    try:
        cases = seed(stack, args.escalations)
        total, elapsed, lat, statuses = asyncio.run(
            fire(stack.api_url, cases, args.concurrency, args.duplicates, args.reads, args.seed))
        drain = wait_for_completion(stack, args.escalations, timeout=600)
        text, ok = render(args, total, elapsed, lat, statuses, drain, db_stats(stack), os.cpu_count())
    finally:
        stack.stop()
    print(text)
    if args.report:
        args.report.write_text(text + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
