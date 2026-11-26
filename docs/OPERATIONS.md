# Operations: metrics, alerts, retention

The pipeline is correct under crashes (Release 1.x) and under load (Release 4.1). This page is
about running it: **how you know it's healthy, what pages someone, and how the tables stop growing
forever.**

| Piece | Where |
|---|---|
| Metrics | `GET /actuator/prometheus` on the Case API (8081) and the worker (8082) |
| Alert rules | [`ops/prometheus/alerts.yml`](../ops/prometheus/alerts.yml), unit-tested in [`alerts.test.yml`](../ops/prometheus/alerts.test.yml) |
| Prometheus | `make up` → http://localhost:9090 (Graph, Alerts) |
| Retention | `OutboxRetention` (Case API) and `DedupeRetention` (worker), hourly |

## Metrics

All CaseGuard metrics carry `application="case-api"` or `application="escalation-worker"`.

| Metric | Type | What it tells you |
|---|---|---|
| `caseguard_outbox_backlog_events` | gauge | Unpublished outbox rows. Going up and down under load is normal |
| `caseguard_outbox_oldest_unpublished_age_seconds` | gauge | How long the **oldest** unpublished event has waited. **The** outbox health signal |
| `caseguard_outbox_published_events_total` | counter | Events published, counted only after the batch is marked and committed |
| `caseguard_outbox_relay_batch_seconds` | timer | One non-empty batch: send all, wait for every ack, mark, commit |
| `caseguard_outbox_relay_failures_total` | counter | Relay ticks that failed (the batch is retried) |
| `caseguard_worker_events_total{outcome}` | counter | `processed` / `duplicate` / `dead_lettered` |
| `caseguard_worker_delivery_failures_total` | counter | Attempts that threw (retried, or dead-lettered if not retryable) |
| `caseguard_worker_processing_seconds` | histogram | Time in the handler (one DB transaction) |
| `caseguard_worker_event_lag_seconds` | histogram | Outbox write → task created: the delay a customer actually feels |
| `kafka_consumer_fetch_manager_records_lag_max` | gauge | Kafka's own consumer lag (registered automatically by Spring Kafka) |
| `caseguard_retention_deleted_total{table}` | counter | Rows removed by retention |
| `caseguard_retention_skipped_total{table,reason}` | counter | Retention runs that refused to delete (see below) |

### Why "oldest age" and not just "backlog"

The backlog can't tell "busy" from "stuck". 500 events that are each 200 ms old is a healthy burst.
3 events that are each 20 minutes old is an outage. Only the age of the oldest event separates the
two, so that's what `OutboxStuck` alerts on.

### Design choices

- **The gauges query the database on each scrape.** Both queries hit the partial index
  `outbox_unpublished_idx`, which only holds unpublished rows, so they're cheap however long the
  history is. If the database is down they report NaN (no data), never a reassuring 0.
- **Counters are incremented after the commit.** A rolled-back relay batch is not "published", so
  the dashboard can't claim work that didn't happen. It's the same rule the agent follows: claims
  need evidence.
- **Empty relay polls are not timed.** The relay polls every 200 ms. Timing empty polls would bury
  the real batches.
- **Metrics only, no custom health check.** A health check that turned DOWN on backlog would make
  orchestrators restart a healthy Case API because Kafka is slow, which makes things worse. Health
  answers "is this process alive?". Alerts answer "is the system doing its job?".
- **Cost:** measured with the load test (1,000 escalations, 2 runs each, same machine, same day).
  Release 4.1 ran at 171–188 req/s and this release at 190–195 req/s, so there's no measurable
  overhead.

## Alerts

Each alert says what a human must do. `page` means act now; `ticket` means fix during working
hours. `alerts.test.yml` proves each alert **fires** in the bad case and **stays quiet** in a
healthy look-alike (for example, a busy outbox with sub-second ages never pages). CI runs
`promtool test rules` on every push.

### OutboxStuck

`max(caseguard_outbox_oldest_unpublished_age_seconds) > 60` for 2 min, severity **page**.
Escalations are being accepted, but no follow-up tasks are being created.

1. Check `OutboxRelayErrors` and the Case API log for `outbox relay tick failed`.
2. Is Kafka up and reachable from the Case API? (`kafka-broker-api-versions.sh`)
3. Nothing is lost: rows stay in the outbox until the broker acknowledges them. Once the cause is
   fixed, the relay drains the backlog by itself (full batches back to back).

### OutboxRelayErrors

The relay failed on every tick for 5 minutes, severity **ticket**. It's usually the cause behind
`OutboxStuck`; seen alone, it's a flapping broker connection. Check the Case API log and the broker.

### WorkerDeadLetters

An event was parked on `<topic>.DLT`, severity **ticket**. These are poison messages that can never
succeed (bad JSON, an unknown escalation, a tenant mismatch), so nothing retries them.

1. Read the message, including its `kafka_dlt-exception-*` headers.
2. Fix the cause (data or code), then republish it to the main topic. This is safe: the worker
   deduplicates on the event id.

### PipelineSlow

p95 of outbox-write → task-created is over 30 s for 10 min, severity **page**. The load test's p95
is about 1 s. Look at `OutboxStuck` and `WorkerFallingBehind` first; then check DB latency via
`caseguard_worker_processing_seconds`.

### WorkerFallingBehind

Kafka consumer lag is over 1,000 for 10 min, severity **ticket**. Run more worker instances (up to
the partition count, 3) or find the slow step in `caseguard_worker_processing_seconds`.

### DedupeRetentionBlocked

The dedupe retention job refused to delete, severity **ticket**. Nothing is wrong *yet*: no
duplicates are possible, but `processed_events` keeps growing. The `reason` label says why:

- `unsafe`: the topic can keep messages longer than the dedupe retention, so raise
  `caseguard.retention.processed-events` or lower the topic's `retention.ms` / `segment.ms`;
- `unknown`: the topic has infinite retention or compaction, or the broker could not be asked.

### ServiceDown

Prometheus can't scrape a service for 2 min, severity **page**. Check the container or process
and its last log lines.

## Retention: the tables stop growing

Two tables grow with every escalation and are only bookkeeping:

| Table | Kept | Why it's safe to delete after that |
|---|---|---|
| `outbox` (published rows only) | 7 days | The broker acknowledged them, so the relay never sends them again. **Unpublished rows are never deleted**: they're events still owed to Kafka |
| `processed_events` (dedupe) | 14 days, **guarded** | Only once no copy of the event can still be in Kafka. This is the hard part, explained below |

Both jobs delete in **small batches** (`LIMIT 1000`, each its own short transaction) using
`FOR UPDATE SKIP LOCKED`, rather than in one big `DELETE`. A big delete holds locks for a long time,
writes a burst of WAL, and loses everything on failure. With small batches, two instances can run
at once without blocking each other, and a run is capped (`max-batches-per-run`) so it can't run
for hours. Migration V7 adds the indexes the deletes need.

### The dedupe trap

`processed_events` is the only thing that recognises a redelivered event. Delete a row while Kafka
can still deliver that event again (a consumer group reset to `earliest`, a replay after an
incident, expired group offsets), and **the event is processed twice**. So a dedupe row may be
deleted only when no copy of its event can still exist in Kafka.

The obvious rule, "Kafka keeps 7 days and we keep 14, so we're covered twice over", is
**wrong**:

- Kafka's `retention.ms` is a **minimum**, not a maximum. Kafka deletes whole log *segments*, and
  only once the newest message in a segment is older than `retention.ms`. The active segment is
  closed only after `segment.ms`. A message can therefore live for about **`retention.ms` +
  `segment.ms`**.
- The broker defaults are 7 days + 7 days, so a message can live **14 days**. The "safe" 14-day
  dedupe retention isn't safe at all.

What CaseGuard does:

1. The topic is created with explicit settings, `retention.ms` = 7 days and `segment.ms` = 1 day, so
   a message lives at most about 8 days.
2. **Before every run**, the worker asks the *broker* (not its own config file) for the topic's
   `retention.ms`, `segment.ms` and `cleanup.policy`. Topic settings can be changed on the broker
   without anyone touching our config.
3. It deletes only if `dedupe retention > retention.ms + segment.ms + margin (1 day)`. With
   14 > 7 + 1 + 1 it deletes.
4. With infinite retention (`-1`), compaction, an unknown topic, or an unreachable broker, it
   **deletes nothing**. Unknown counts as unsafe: a growing table is a cost, a duplicate is a bug.
5. If it all failed anyway, `follow_up_tasks` has a `UNIQUE (escalation_id)` constraint, so a late
   duplicate fails loudly and lands in the dead-letter topic instead of creating a second task.

The tests run against **real topics** created with dangerous settings: broker defaults (7 d + 7 d),
infinite retention, compaction, and a topic that doesn't exist. Each one must make the job refuse.
A mutation check (computing the lifetime from `retention.ms` alone) fails two of them.

## Proven where

| Claim | Proof |
|---|---|
| Retention deletes only old published outbox rows, in bounded batches | `case-api/.../OperationsTest` (6 tests) |
| Dedupe retention refuses every unsafe topic setting; dedupe still works after a purge | `escalation-worker/.../WorkerOperationsTest` (9 tests, real broker) |
| Gauges and counters tell the truth (backlog to 0 after relay; counters only after commit) | `OperationsTest`, `WorkerOperationsTest` |
| The dashboards agree with reality during crashes | Harness S04: the restarted worker shows `duplicate +1, processed +0`. S05: `processed +1, duplicate +1`, the relay re-sent exactly 1, and the backlog is 0 |
| Alerts fire when they should and stay quiet when they shouldn't | `ops/prometheus/alerts.test.yml` (`promtool test rules`, CI job `alert-rules`) |
| Prometheus really scrapes both services and loads every rule | CI `docker-compose` job: the targets are up, 7 rules are healthy, and the worker's `processed` count is ≥ 1 |

## Not done (would be next)

- **Alertmanager routing** (who gets paged, deduplication, silences): environment-specific.
- **SLOs with burn-rate alerts** (for example, "99% of escalations reach the queue within 30 s")
  instead of fixed thresholds. `caseguard_worker_event_lag_seconds` is already a histogram, so it
  could carry this.
- **Dashboards as code** (Grafana JSON). Prometheus' own UI is enough for the demo.
- **Archiving** instead of deleting, if an audit needs the delivery history.
