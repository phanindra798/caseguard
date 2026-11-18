-- V3: tables written by the escalation worker.
-- The Case API owns ALL schema migrations (one owner = no conflicting migrations),
-- even for tables only the worker writes.

-- Deduplication: one row per event the worker has processed.
-- The worker inserts here in the SAME transaction as its work; a redelivered
-- event hits this primary key and is skipped.
create table processed_events (
  event_id      uuid primary key,          -- = outbox.id of the event
  processed_at  timestamptz not null default now()
);

-- The follow-up task a human support team will work on.
create table follow_up_tasks (
  id             uuid primary key,
  tenant_id      text not null,
  escalation_id  uuid not null references escalations(id),
  queue          text not null,            -- which support team, e.g. 'tier2-support'
  created_at     timestamptz not null default now(),
  -- Second safety net: even if the dedupe logic had a bug, one task per escalation.
  constraint follow_up_tasks_one_per_escalation unique (escalation_id)
);
