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
| 1.2 | Idempotent escalation endpoint + transactional outbox | ⏳ Next |
| 1.3 | Outbox relay, Redpanda, worker with dedupe, crash fail points | |
| 1.4 | LangGraph agent with planner, verify, reconcile | |
| 1.5 | Regression harness + CI | |

## Run it locally

Requirements: Java 21, Maven, Docker (for PostgreSQL).

```bash
make db
```

```bash
make test
```

```bash
make run
```

Then call the API as a customer:

```bash
curl -H "X-Tenant-Id: acme" -H "X-Customer-Id: cust-A1" http://localhost:8081/orders/1042
```

Another company's order returns `404`, exactly like an order that doesn't exist:

```bash
curl -H "X-Tenant-Id: acme" -H "X-Customer-Id: cust-A1" http://localhost:8081/orders/2001
```

## Case API (so far)

| Endpoint | Returns |
|---|---|
| `GET /orders/{id}` | The caller's order, or 404 |
| `GET /cases/{id}` | The caller's case (with `version`), or 404 |
| `GET /cases?orderId=...` | The caller's cases for that order (empty list if not theirs) |
| `GET /actuator/health` | Service health |

Every request must carry `X-Tenant-Id` and `X-Customer-Id` (set from the trusted session, never by the LLM), otherwise `400`.

## Design decisions so far

- **Ownership in every query, not just in the agent.** Lookups filter by `id AND tenant_id AND customer_id`. Agent guardrails are an extra layer, not the security boundary.
- **404, not 403, for other people's data.** The response doesn't reveal that the record exists.
- **The database enforces tenant consistency too.** Composite foreign keys `(tenant_id, customer_id)` make it impossible to store an order whose customer belongs to another tenant.
- **Flyway owns the schema; Hibernate only validates it** (`ddl-auto: validate`).
- **Money as integer cents** (`bigint`), never floating point.
- **Demo data is separate from migrations** (`db/seed` vs `db/migration`), so production can leave it out.
