-- V6: carry the request's trace context through the outbox.
-- The relay publishes from a timer thread, outside the original HTTP request. Without this column,
-- every Kafka message would start a brand-new, unconnected trace.
alter table outbox add column trace_parent text;   -- W3C traceparent, e.g. 00-<trace id>-<span id>-01
