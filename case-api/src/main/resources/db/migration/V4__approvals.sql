-- V4: supervisor approvals for escalations of expensive orders.
--
-- An approval is BOUND to one exact action:
--   operation_id   the escalation it authorises (the same id the escalation request will carry)
--   case_version   the case state the supervisor saw
--   policy_ref     the policy version that required it
-- If any of these differ when the escalation is requested, the approval does not count.

create table approvals (
  id                uuid primary key,
  tenant_id         text not null,
  customer_id       text not null,
  case_id           text not null references cases(id),
  operation_id      uuid not null,
  case_version      int  not null,
  policy_ref        text not null,
  reason            text not null,
  conversation_ref  text,                      -- opaque id the agent uses to resume the right conversation
  status            text not null check (status in ('PENDING', 'APPROVED', 'REJECTED')),
  decided_by        text,
  decided_at        timestamptz,
  created_at        timestamptz not null default now(),
  expires_at        timestamptz not null,
  foreign key (tenant_id, customer_id) references customers(tenant_id, id),
  constraint approvals_operation_id_key unique (operation_id)   -- one approval per operation
);

create index approvals_pending_idx on approvals (tenant_id, created_at) where status = 'PENDING';
