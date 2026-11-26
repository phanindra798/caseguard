package dev.caseguard.caseapi.outbox;

import dev.caseguard.caseapi.reliability.FailPoints;
import dev.caseguard.caseapi.tracing.TraceContext;
import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.context.Scope;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.json.JsonMapper;

/**
 * Publishes unpublished outbox rows to Kafka, then marks them published.
 *
 * <p>Guarantee: <b>at-least-once</b>. A row is marked published only AFTER the broker has
 * acknowledged it, so a crash can cause a duplicate publish but never a lost event.
 * Consumers deduplicate on the event id (= outbox row id), which never changes.
 *
 * <pre>
 *   BEGIN
 *     SELECT ... WHERE published_at IS NULL ... FOR UPDATE SKIP LOCKED   (claim a batch)
 *     send EVERY row to Kafka without waiting in between (the producer batches them)
 *     WAIT until the broker has acknowledged ALL of them
 *     UPDATE outbox SET published_at = now() WHERE id = ANY(batch)       (one statement)
 *   COMMIT
 *
 * If any send fails, nothing is marked and the whole batch is sent again next time: duplicates
 * are possible, losses are not. (Load test: waiting for each ack one by one made the relay the
 * bottleneck, with a publish lag of seconds; batching removed it.)
 * </pre>
 *
 * <p>{@code SKIP LOCKED} lets several relay instances run in parallel: each one skips rows
 * another instance has already locked, so the same row isn't claimed twice at the same time.
 */
@Component
public class OutboxRelay {

  public static final String FAIL_AFTER_PUBLISH_BEFORE_MARK = "RELAY_AFTER_PUBLISH_BEFORE_MARK";
  private static final Logger log = LoggerFactory.getLogger(OutboxRelay.class);

  private final JdbcTemplate jdbc;
  private final TransactionTemplate tx;
  private final KafkaTemplate<String, String> kafka;
  private final FailPoints failPoints;
  private final JsonMapper json;
  private final String topic;
  private final int batchSize;
  private final Counter published;
  private final Timer batchTimer;

  public OutboxRelay(JdbcTemplate jdbc, TransactionTemplate tx, KafkaTemplate<String, String> kafka,
                     FailPoints failPoints, JsonMapper json, MeterRegistry registry,
                     @Value("${caseguard.topics.escalation-requested}") String topic,
                     @Value("${caseguard.relay.batch-size:100}") int batchSize) {
    this.jdbc = jdbc;
    this.tx = tx;
    this.kafka = kafka;
    this.failPoints = failPoints;
    this.json = json;
    this.topic = topic;
    this.batchSize = batchSize;
    this.published = Counter.builder("caseguard.outbox.published")
        .description("Outbox events published to Kafka (counted after the batch is marked and committed)")
        .baseUnit("events")
        .register(registry);
    this.batchTimer = Timer.builder("caseguard.outbox.relay.batch")
        .description("Time to publish one non-empty batch: send all, wait for every ack, mark, commit")
        .register(registry);
  }

  public int batchSize() {
    return batchSize;
  }

  private record Row(UUID id, UUID aggregateId, String eventType, String payload, OffsetDateTime createdAt,
                     String traceParent) {}

  /**
   * Publish one batch. Returns how many rows were published.
   *
   * <p>Uses TransactionTemplate (not @Transactional) on purpose: the scheduler calls this
   * method on the same object, and a self-call bypasses Spring's @Transactional proxy.
   */
  public int relayBatch() {
    Timer.Sample sample = Timer.start();
    Integer sent = tx.execute(status -> {
      List<Row> rows = jdbc.query("""
          select id, aggregate_id, event_type, payload::text, created_at, trace_parent
            from outbox
           where published_at is null
           order by created_at
           limit ?
             for update skip locked
          """,
          (rs, i) -> new Row(rs.getObject(1, UUID.class), rs.getObject(2, UUID.class), rs.getString(3),
              rs.getString(4), rs.getObject(5, OffsetDateTime.class), rs.getString(6)),
          batchSize);

      if (rows.isEmpty()) {
        return 0;
      }
      List<CompletableFuture<?>> acks = rows.stream().<CompletableFuture<?>>map(this::send).toList();
      awaitAll(acks);                                     // every message acknowledged by the broker
      failPoints.hit(FAIL_AFTER_PUBLISH_BEFORE_MARK);     // crash here → rows stay unpublished → re-sent
      jdbc.update("update outbox set published_at = now() where id = any(?)",
          (Object) rows.stream().map(Row::id).toArray(UUID[]::new));
      log.info("published {} event(s)", rows.size());
      return rows.size();
    });
    int n = sent == null ? 0 : sent;
    if (n > 0) {                          // empty polls every 200 ms would drown the real batches
      sample.stop(batchTimer);
      published.increment(n);             // only after COMMIT: a rolled-back batch is not counted
    }
    return n;
  }

  /** Start sending one row; returns the broker acknowledgement as a future (doesn't wait). */
  private CompletableFuture<?> send(Row row) {
    // Envelope: the event id travels in the message AND as a header, so any consumer can dedupe.
    Map<String, Object> envelope = new LinkedHashMap<>();
    envelope.put("eventId", row.id().toString());
    envelope.put("eventType", row.eventType());
    envelope.put("occurredAt", row.createdAt().toString());
    envelope.put("payload", json.readTree(row.payload()));

    // Key = escalation id → all events for one escalation land on the same partition, in order.
    ProducerRecord<String, String> record =
        new ProducerRecord<>(topic, row.aggregateId().toString(), json.writeValueAsString(envelope));
    record.headers().add("event-id", row.id().toString().getBytes(StandardCharsets.UTF_8));
    record.headers().add("event-type", row.eventType().getBytes(StandardCharsets.UTF_8));

    // Continue the ORIGINAL request's trace (stored in the outbox row), not the relay's timer thread.
    // The span is current while send() runs, so the Kafka instrumentation puts it in the message headers.
    Span span = TraceContext.startContinuing(row.traceParent(), "outbox.publish " + row.eventType());
    try (Scope ignored = span.makeCurrent()) {
      return kafka.send(record).whenComplete((result, error) -> span.end());
    } catch (RuntimeException e) {
      span.end();
      throw new IllegalStateException("publish failed for outbox row " + row.id(), e);
    }
  }

  private static void awaitAll(List<CompletableFuture<?>> acks) {
    try {
      CompletableFuture.allOf(acks.toArray(CompletableFuture[]::new)).get(10, TimeUnit.SECONDS);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("interrupted while publishing", e);
    } catch (Exception e) {
      // Throwing rolls back the batch: nothing is marked published, everything is retried next time.
      throw new IllegalStateException("publish failed; the batch will be retried", e);
    }
  }

}
