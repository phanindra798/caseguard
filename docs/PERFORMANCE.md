# Performance: load test of the escalation pipeline

`make loadtest` (`python -m caseguard_harness.loadtest`) drives the **real** pipeline (Case API →
PostgreSQL → outbox relay → Kafka → escalation worker) with 2,000 escalation requests at concurrency 50,
plus **20% retries with the same idempotency key** and 25% reads, shuffled so retries race their originals.
It measures HTTP latency, the **outbox publish lag** (row written → published to Kafka) and the
**end-to-end lag** (escalation accepted → follow-up task created), and checks the invariants.

**Machine:** 4 CPUs, 15 GB, shared by PostgreSQL, Kafka, both Java services and the load generator. The
numbers show relative behaviour and bottlenecks, not production capacity.

## Invariants under load (every run)

2,000 escalations → exactly 2,000 escalation rows, 2,000 outbox events (all published), 2,000 processed
events and 2,000 follow-up tasks; no server errors; every retry returned 200 (replay) or won the race with
202 (about 207 per run, where the original then got the replay). Never a duplicate.

## What was slow, and what changed

| Step | Finding | Change |
|---|---|---|
| 1 | Publish lag **3.5 s p50 / 6.0 s p95**: almost all of the end-to-end lag. The relay waited for Kafka's acknowledgement **one message at a time**, and slept 200 ms between batches even with a backlog | Send the whole batch, wait once for all acks, mark the batch with **one** `UPDATE … WHERE id = ANY(…)`; drain full batches without sleeping. Same at-least-once guarantee (any failed send → nothing marked → batch re-sent) |
| 2 | Publish lag now ~0.2 s, but end-to-end p95 still ~1.4 s: the **worker** processed events on a single thread although the topic has 3 partitions | `spring.kafka.listener.concurrency: 3` (one thread per partition; per-escalation ordering kept) |

## Results

Step 1, single runs (the effect is far larger than the noise):

| | Before | After batching |
|---|---|---|
| outbox publish lag p50 / p95 | 3,492 / 5,973 ms | **111 / 216 ms** |
| end-to-end lag p50 / p95 | 3,852 / 6,156 ms | 216 / 1,471 ms |
| backlog drained after the last request | 6.7 s | **0.5 s** |
| HTTP throughput | 187 req/s | 222 req/s |

Step 2, **three runs each** (median, range):

| Worker threads | end-to-end p50 | end-to-end p95 | throughput |
|---|---|---|---|
| 1 | 232 ms (218–236) | 1,429 ms (1,367–1,777) | 213 req/s (195–213) |
| 3 | **156 ms** (156–161) | **1,103 ms** (418–1,151) | **242 req/s** (240–261) |

Overall: end-to-end p50 **3.9 s → 0.16 s**, p95 **6.2 s → ~1.1 s**, with every invariant intact.

**Lesson:** the first run with 3 worker threads showed p95 = 418 ms, which would have looked like a 3.5×
gain. Two repeats gave 1,151 and 1,103 ms. On a shared machine p95 is noisy: compare distributions of
repeated runs, not single numbers. The relay change is a clear win; the worker change is a real but modest one.

## Cost of the metrics (Release 4.2)

Release 4.2 added Micrometer metrics on the request, relay and worker paths. Measured the same way (1,000
escalations, concurrency 50), with the 4.1 and 4.2 jars run on the same machine on the same day:

| Build | Throughput (2 runs) | End-to-end p95 (2 runs) |
|---|---|---|
| 4.1 (no Prometheus registry) | 171, 188 req/s | 1,187, 1,128 ms |
| 4.2 (metrics on) | 190, 195 req/s | 1,276, 1,073 ms |

No measurable overhead. Both are below the 4.1 report's 242 req/s, which was measured on a different day: the
machine was slower, not the code. A before/after comparison needs both builds on the same machine at the same time.

## Next bottlenecks (not done)

- API latency p95 ~0.6–0.8 s at concurrency 50 on 4 shared CPUs: profile the request path (connection pool
  size, Hibernate flushes) on a machine where the load generator doesn't compete for CPU.
- The relay holds its database transaction while waiting for Kafka acks; at much higher volumes, mark rows
  in a separate short transaction, or move to change-data-capture (Debezium).
- ~~`processed_events` grows forever: add a retention job.~~ Done in Release 4.2, with a guard that checks the
  broker's topic settings first ([`OPERATIONS.md`](OPERATIONS.md)).
