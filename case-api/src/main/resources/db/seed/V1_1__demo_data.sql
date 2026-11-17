-- Demo data for local runs and tests. Lives in db/seed, NOT db/migration,
-- so a production deployment can leave it out (spring.flyway.locations).
--
-- Two tenants (acme, globex) so every test can try to cross the tenant boundary.
-- Dates are relative to when the seed runs, so "days late" stays realistic on a fresh database.

insert into tenants (id, name) values
  ('acme',   'Acme Retail'),
  ('globex', 'Globex Outdoor');

insert into customers (id, tenant_id, name, region) values
  ('cust-A1', 'acme',   'Asha Rao',     'US'),
  ('cust-A2', 'acme',   'Ben Okafor',   'EU'),
  ('cust-G1', 'globex', 'Chen Wei',     'US');

insert into orders (id, tenant_id, customer_id, description, amount_cents, status, promised_date) values
  -- cust-A1: late, cheap order (happy-path escalation)
  ('1042', 'acme',   'cust-A1', 'Blue rain jacket',        12000, 'SHIPPED', current_date - 10),
  -- cust-A1: only slightly late (should NOT be escalated)
  ('1043', 'acme',   'cust-A1', 'Wool socks (3 pack)',      2500, 'SHIPPED', current_date - 2),
  -- cust-A1: second late order (ambiguity / wrong-case scenarios)
  ('1050', 'acme',   'cust-A1', 'Trail running shoes',     14000, 'SHIPPED', current_date - 9),
  -- cust-A1: expensive late order (needs supervisor approval later)
  ('1099', 'acme',   'cust-A1', 'Camping tent (4 person)', 75000, 'SHIPPED', current_date - 12),
  -- cust-A2: same tenant, different customer (customer-level isolation)
  ('1200', 'acme',   'cust-A2', 'Hiking backpack',          9000, 'SHIPPED', current_date - 8),
  -- cust-G1: other tenant (tenant-level isolation)
  ('2001', 'globex', 'cust-G1', 'Kayak paddle',            18000, 'SHIPPED', current_date - 15);

insert into cases (id, tenant_id, customer_id, order_id, subject, status) values
  ('C-1042', 'acme',   'cust-A1', '1042', 'Where is my jacket?',  'OPEN'),
  ('C-1043', 'acme',   'cust-A1', '1043', 'Socks running late',   'OPEN'),
  ('C-1050', 'acme',   'cust-A1', '1050', 'Shoes not arrived',    'OPEN'),
  ('C-1099', 'acme',   'cust-A1', '1099', 'Tent delivery delay',  'OPEN'),
  ('C-1200', 'acme',   'cust-A2', '1200', 'Backpack missing',     'OPEN'),
  ('C-2001', 'globex', 'cust-G1', '2001', 'Paddle not delivered', 'OPEN');
