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
| 1.3 | Outbox relay, Redpanda, worker with dedupe, crash fail points | ⏳ Next |
| 1.4 | LangGraph agent with planner, verify, reconcile | |
| 1.5 | Regression harness + CI | |

## Run it locally

Requirements: Docker. (Java 21 only if you want to run tests outside Docker; Maven is **not** needed, the `./mvnw` wrapper downloads it.)

Start PostgreSQL and the Case API:

```bash
make up
```

Run the tests (needs PostgreSQL running, e.g. `make db`):

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

API docs (Swagger UI): http://localhost:8081/swagger-ui.html

## Case API

| Endpoint | Returns |
|---|---|
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

Tests were also verified by **breaking the code on purpose**: removing the ownership filter fails 4 tests, and removing the Java "already open" check still passes because the **database's partial unique index** blocks duplicates on its own.

## Design decisions

- **Ownership in every query, not just in the agent.** Lookups filter by `id AND tenant_id AND customer_id`. Agent guardrails are an extra layer, not the security boundary.
- **404, not 403, for other people's data.** The response doesn't reveal that the record exists.
- **Idempotency key created by the caller's code, enforced by a `UNIQUE` constraint.** Retries return the original result.
- **One open escalation per case, enforced by a partial unique index** (`WHERE status <> 'FAILED'`), with the Java check as a fast path and a race-recovery path that re-reads after a constraint violation.
- **Transactional outbox.** The escalation and its `EscalationRequested` event are written in one transaction, so neither can exist without the other. A relay publishes them in Phase 1.3.
- **Fail points.** Named spots in the code where tests can force a crash at an exact moment.
- **Flyway owns the schema; Hibernate only validates it.** Money is integer cents. Demo data is kept separate from migrations.
