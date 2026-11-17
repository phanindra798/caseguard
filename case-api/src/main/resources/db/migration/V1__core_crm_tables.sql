-- V1: core CRM tables for CaseGuard.
-- Flyway runs this once, in order, and records it in flyway_schema_history.
-- Never edit a migration after it has run; add V2, V3, ... instead.

create table tenants (
  id    text primary key,                 -- e.g. 'acme', 'globex'
  name  text not null
);

create table customers (
  id         text primary key,
  tenant_id  text not null references tenants(id),
  name       text not null,
  region     text not null,               -- picks which policy applies (later phases)
  -- Lets other tables reference (tenant_id, customer_id) as a pair,
  -- so the database itself guarantees an order's customer is in the same tenant.
  unique (tenant_id, id)
);

create table orders (
  id             text primary key,
  tenant_id      text not null,
  customer_id    text not null,
  description    text not null,
  amount_cents   bigint not null check (amount_cents >= 0),   -- money as integer cents, never float
  status         text not null check (status in ('PLACED', 'SHIPPED', 'DELIVERED', 'CANCELLED')),
  promised_date  date not null,
  delivered_at   timestamptz,
  foreign key (tenant_id, customer_id) references customers(tenant_id, id),
  unique (tenant_id, id)
);

create table cases (
  id           text primary key,
  tenant_id    text not null,
  customer_id  text not null,
  order_id     text not null,
  subject      text not null,
  status       text not null check (status in ('OPEN', 'ESCALATED', 'CLOSED')),
  version      int  not null default 0,   -- bumped on every change (optimistic locking)
  updated_at   timestamptz not null default now(),
  foreign key (tenant_id, customer_id) references customers(tenant_id, id),
  foreign key (tenant_id, order_id)    references orders(tenant_id, id)
);

-- Every lookup filters by tenant + customer, so index for exactly that.
create index orders_owner_idx on orders (tenant_id, customer_id);
create index cases_owner_idx  on cases  (tenant_id, customer_id);
create index cases_order_idx  on cases  (order_id);
