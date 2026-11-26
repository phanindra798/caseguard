-- V7: indexes for the retention jobs (Release 4.2).
-- Without them, "delete the oldest rows" would scan the whole table every hour.

-- Worker dedupe table: retention deletes by age.
create index processed_events_processed_at_idx on processed_events (processed_at);

-- Outbox: only PUBLISHED rows are ever deleted. A partial index holds just those rows.
-- (Unpublished rows already have their own partial index from V2, used by the relay.)
create index outbox_published_at_idx on outbox (published_at) where published_at is not null;
