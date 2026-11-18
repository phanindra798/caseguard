# CaseGuard

**A tool-using AI support agent for CRM escalations, plus a regression harness that catches the agent when what it says disagrees with what actually happened.**

> 🚧 Work in progress, built phase by phase. See [`docs/PROJECT_GUIDE.md`](docs/PROJECT_GUIDE.md) for the full design.

## The problem

AI support agents don't just talk; they take actions. In production they can:

- claim "your case is escalated" when the backend call actually failed;
- create duplicate tasks when they retry after a timeout;
- get quietly worse after a prompt or model change.

CaseGuard pairs an agent that **verifies before it claims** with a backend that is **idempotent under crashes and replays**, and a **harness** that checks the agent's reply, its tool calls, and the real database state, under injected failures.

## Status

| Phase | What | Status |
|---|---|---|
| 1.1 | Case API: orders and cases with tenant/customer ownership checks | ✅ Done |
| 1.2 | Idempotent escalation endpoint + transactional outbox, concurrency and atomicity tests, OpenAPI contract | ✅ Done |
| 1.3 | Outbox relay → Kafka → worker with dedupe, dead-letter topic, **real crash-and-replay demo** | ✅ Done |
| 1.4 | LangGraph agent: planner loop, eligibility in code, operation ids, reconcile, verify-before-claim | ✅ Done |
| 1.5 | Regression harness: YAML scenarios, fault injection, claim checker | ⏳ Next |

## The agent

A LangGraph state machine. **The LLM decides what to find out and what to propose; code decides what is allowed and what is true.**

```
guardrail → plan ⇄ read tools (LLM chooses: list_orders, get_order, get_cases, search_policy)
          → check_eligibility (code: business days, policy, approval limit)
          → prepare_action (operation id per action, saved before the write)
          → execute (same id on every retry) → reconcile (after timeouts) → verify (poll)
          → compose_reply (claims allowed only from evidence for THIS escalation and case)
```

A real conversation against the running system (`make chat`):

```
you>   My order is late, please escalate it.
   [plan] call_tool list_orders: customer didn't name an order; list their orders
   [plan] ask_customer: 4 late orders and the customer didn't say which
agent> I can see more than one delayed order: #1043 (Wool socks), #1042 (Blue rain jacket), ... Which one do you mean?

you>   The blue jacket, order 1042.
   [plan] call_tool get_order: customer named order 1042
   [plan] call_tool get_cases: need the support case for order 1042
   [plan] call_tool search_policy: need the escalation policy that applies to this customer
   [plan] propose_escalation: order, case and policy are known and the customer asked for an escalation
   [tool] POST /cases/C-1042/escalations -> 202 ok
   [tool] GET /escalations/d778e20b-... -> 200 ok        (REQUESTED)
   [tool] GET /escalations/d778e20b-... -> 200 ok        (COMPLETED: the worker created the task)
agent> I've escalated order 1042 to our tier-2 support team and a specialist task has been created
       (reference d778e20b-...). It is 8 business days late, which qualifies under policy ESC-3@v2.

you>   Order 2001 is late, escalate it.
   [tool] GET /orders/2001 -> 404 not_found
agent> I couldn't find order 2001 on your account.
```

**LLM modes:** `scripted` (default) is a deterministic rule-based brain used for tests and CI. `live` uses any chat model through LangChain's provider-neutral `init_chat_model` (set `CASEGUARD_LLM=live` and `CASEGUARD_MODEL=<provider>:<model>`, and install that provider's LangChain package). No provider is hardcoded.

## How an escalation flows

```
 POST /cases/C-1042/escalations          (Idempotency-Key)
        │
        ▼
 ┌──────────────┐  one DB transaction   ┌────────────────────────────┐
 │  Case API    │ ────────────────────► │ escalations + outbox rows  │
 └──────────────┘                       └─────────────┬──────────────┘
        ▲                                             │ outbox relay (every 200 ms)
        │ GET /escalations/{id}                       ▼   publish, wait for ack, mark published
        │ (agent verifies)                    ┌───────────────────────┐
        │                                     │ Kafka                 │
        │                                     │ escalation.requested  │──► .DLT (poison messages)
        │                                     └───────────┬───────────┘
        │                                                 ▼
        │                                     ┌───────────────────────┐  one DB transaction:
        └──────── status = COMPLETED ──────── │ Escalation worker     │  dedupe row + task + status
                                              └───────────────────────┘  THEN ack the Kafka offset
```

**Guarantee:** at-least-once delivery + deduplicated, transactional processing = **exactly one follow-up task per escalation**, even when the relay or the worker crashes mid-flight.

## The crash-and-replay demo

```bash
make infra
```

```bash
make demo-worker
```

The worker is **actually killed** (`Runtime.halt`, like a power cut) right after committing its work but before acknowledging Kafka. On restart, Kafka delivers the same event again, the worker recognises its event id and skips it, and the final state is still **one** task:

```
▶ Wait for worker-1 to die
  ✓ worker-1 HALTED (exit code 1): FAIL_POINT triggered (halt): WORKER_AFTER_DB_COMMIT_BEFORE_ACK
▶ State right after the crash
  follow_up_tasks  = 1
  processed_events = 1
  → the work was COMMITTED, but Kafka never got the ack, so it will deliver the event again.
▶ Restart the worker (no fail point)
  ✓ worker-2 got the SAME event again: duplicate event fb4a5025-… skipped
▶ Final check
  ✓ PASS: delivered twice, exactly ONE task (scenario: worker)
```

`make demo-relay` does the same for the relay: it dies after publishing but before marking the outbox row, re-sends the same event on restart, and the worker skips it. **Both demos run in CI on every push.**

## Run it locally

Requirements: Docker, and Java 21 for tests and demos. Maven is **not** needed: the `./mvnw` wrapper downloads it.

Start the whole stack (PostgreSQL, Kafka, Case API, worker, agent on :8090):

```bash
make up
```

Run all tests (needs PostgreSQL and Kafka, e.g. `make infra`):

```bash
make test
```

Request an escalation as customer `cust-A1` of tenant `acme`:

```bash
curl -i -X POST http://localhost:8081/cases/C-1042/escalations \
  -H "X-Tenant-Id: acme" -H "X-Customer-Id: cust-A1" \
  -H "Idempotency-Key: 6f1c2a52-8a5e-4c1e-9f4e-2b8f3c1d9a01" \
  -H "Content-Type: application/json" \
  -d '{"reason":"Order is 8 business days late","policyRef":"ESC-3@v2"}'
```

Run the same command again: you get `200` with `Idempotent-Replayed: true` and the **same** escalation, not a second one.

Talk to the agent over HTTP:

```bash
curl -s -X POST localhost:8090/chat -H "Content-Type: application/json" \
  -d '{"session":"demo-asha","message":"Order 1042 is late, please escalate it."}'
```

Or in the terminal (Python 3.12+):

```bash
make agent-venv
```

```bash
make chat
```

API docs (Swagger UI): http://localhost:8081/swagger-ui.html

## Case API

| Endpoint | Returns |
|---|---|
| `GET /orders` | The caller's own orders |
| `GET /orders/{id}` | The caller's order, or 404 |
| `GET /cases/{id}` | The caller's case (with `version`), or 404 |
| `GET /cases?orderId=...` | The caller's cases for that order (empty if not theirs) |
| `POST /cases/{id}/escalations` | **202** new · **200** replay · **409** `already_open` / `case_not_open` / `operation_conflict` · **404** · **400** |
| `GET /escalations/{id}` | The caller's escalation and its status (agent's *verify* step) |
| `GET /escalations?operationId=...` | The escalation an operation created, or empty (agent's *reconcile* step) |

Every request must carry `X-Tenant-Id` and `X-Customer-Id` (set from the trusted session, never by the LLM). Errors are [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) ProblemDetail JSON with a stable machine-readable `code`. The full contract is committed as [`case-api/openapi.json`](case-api/openapi.json) and checked by a test.

## What the tests prove

| Test | Proof |
|---|---|
| Ownership (10) | Other tenants' and other customers' data is invisible; "not yours" looks exactly like "doesn't exist"; the DB rejects cross-tenant rows |
| Escalation API (12) | New → 202 with 1 escalation + 1 outbox event; same key → 200 replay with **no** extra rows; other key → 409; key reuse on another case → 409; failed escalations allow a retry |
| Concurrency (6) | **20 simultaneous identical requests → exactly 1 escalation and 1 event**; 20 competing requests → one 202, nineteen 409 |
| Atomicity (1) | A forced failure *after* the escalation insert rolls back everything; a retry with the same key then succeeds |
| Contract (1) | The served OpenAPI spec equals the committed `openapi.json` |
| Outbox relay (3) | Events reach Kafka carrying their event id; a crash after publish re-sends the **same** id; 4 relays at once never double-publish (`FOR UPDATE SKIP LOCKED`) |
| Worker (7) | One event → one task; duplicate delivery skipped; crash after commit → redelivered and skipped; crash before commit → rolled back and redone; poison message → dead-letter topic without blocking the partition; wrong tenant rejected; `UNIQUE(escalation_id)` as the last safety net |
| Crash demos (2) | Real JVM kills of the worker and the relay; exactly one task afterwards |
| Agent scenarios (18) | Happy path; not eligible; worker down → "pending"; timeout → retry with the **same** operation id; all retries time out → **reconcile** finds the write, no fallback; confirmed not created; still unknown → honest "couldn't confirm"; ambiguous → asks, then proceeds; no unnecessary questions; second case gets a **new** id and another case's proof is never used; already escalated; approval needed; other tenant → 404; prompt injection; LLM can't call write tools or change identity; step budget; over-claiming model caught |
| Agent units (6) | Business days skip weekends and holidays; expired policy never chosen; region filter beats keyword match |
| Agent end-to-end (5) | Against the **real** Case API + Kafka + worker: escalation verified `REQUESTED → COMPLETED`; second conversation gets `already_open`; decline; cross-tenant 404; ambiguity uses the real order list |

Tests were also verified by **breaking the code on purpose**: removing the ownership filter fails 4 tests; disabling the worker's dedupe fails the duplicate and crash tests; reusing one operation id for a whole conversation fails the two-case test; a new id per retry fails both retry tests; and removing the Java "already open" check still passes because the **database's partial unique index** blocks duplicates on its own.

## Design decisions

- **Ownership in every query, not just in the agent.** Lookups filter by `id AND tenant_id AND customer_id`. Agent guardrails are an extra layer, not the security boundary.
- **404, not 403, for other people's data.** The response doesn't reveal that the record exists.
- **Idempotency key created by the caller's code, enforced by a `UNIQUE` constraint.** Retries return the original result.
- **One open escalation per case, enforced by a partial unique index** (`WHERE status <> 'FAILED'`), with the Java check as a fast path and a race-recovery path that re-reads after a constraint violation.
- **Transactional outbox.** The escalation and its `EscalationRequested` event are written in one transaction, so neither can exist without the other. The relay publishes with `acks=all`, waits for the broker's confirmation, and only then marks the row, so duplicates are possible but losses are not.
- **Commit, then ack.** The worker commits its database transaction (dedupe row + task + status) before committing the Kafka offset. A crash in between causes a harmless redelivery, never lost work.
- **Poison messages go to a dead-letter topic** instead of being retried forever and blocking the partition.
- **Apache Kafka 4 in KRaft mode** (no ZooKeeper): the official image runs as a single node locally and in CI.
- **Fail points.** Named spots in the code where tests can force a crash at an exact moment.
- **Flyway owns the schema; Hibernate only validates it.** Money is integer cents. Demo data is kept separate from migrations.
