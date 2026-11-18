-- V2: escalation requests and the transactional outbox.

create table escalations (
  id            uuid primary key,
  tenant_id     text not null,
  customer_id   text not null,                -- copied from the case, so ownership checks need no join
  case_id       text not null,
  operation_id  uuid not null,                -- the Idempotency-Key, created ONCE by the agent's code
  status        text not null check (status in ('REQUESTED', 'COMPLETED', 'FAILED')),
  reason        text not null,
  policy_ref    text not null,                -- e.g. 'ESC-3@v2'
  requested_at  timestamptz not null default now(),
  completed_at  timestamptz,
  foreign key (tenant_id, customer_id) references customers(tenant_id, id),
  foreign key (case_id) references cases(id),
  -- Idempotency: one escalation per operation, ever.
  constraint escalations_operation_id_key unique (operation_id)
);

-- Business rule: at most ONE non-failed escalation per case.
-- A partial unique index applies only to rows matching the WHERE clause,
-- so a FAILED escalation does not block a new attempt.
create unique index escalations_one_open_per_case
  on escalations (case_id) where status <> 'FAILED';

create index escalations_owner_idx on escalations (tenant_id, customer_id);

-- Transactional outbox: "messages to publish", written in the SAME transaction
-- as the business row. A relay (Phase 1.3) publishes them to Redpanda.
create table outbox (
  id            uuid primary key,             -- this IS the event_id consumers use to deduplicate
  tenant_id     text not null,
  aggregate_id  uuid not null,                -- the escalation this event is about
  event_type    text not null,
  payload       jsonb not null,
  created_at    timestamptz not null default now(),
  published_at  timestamptz                   -- null = not yet published
);

-- The relay asks "oldest unpublished rows first". A partial index keeps that fast
-- because it only contains the (few) unpublished rows.
create index outbox_unpublished_idx on outbox (created_at) where published_at is null;
