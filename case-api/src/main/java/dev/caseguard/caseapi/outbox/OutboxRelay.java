package dev.caseguard.caseapi.outbox;

import dev.caseguard.caseapi.reliability.FailPoints;
import java.nio.charset.StandardCharsets;
import java.time.OffsetDateTime;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
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
 *     for each row:  send to Kafka, WAIT for broker ack  →  UPDATE published_at = now()
 *   COMMIT
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

  public OutboxRelay(JdbcTemplate jdbc, TransactionTemplate tx, KafkaTemplate<String, String> kafka,
                     FailPoints failPoints, JsonMapper json,
                     @Value("${caseguard.topics.escalation-requested}") String topic,
                     @Value("${caseguard.relay.batch-size:100}") int batchSize) {
    this.jdbc = jdbc;
    this.tx = tx;
    this.kafka = kafka;
    this.failPoints = failPoints;
    this.json = json;
    this.topic = topic;
    this.batchSize = batchSize;
  }

  private record Row(UUID id, UUID aggregateId, String eventType, String payload, OffsetDateTime createdAt) {}

  /**
   * Publish one batch. Returns how many rows were published.
   *
   * <p>Uses TransactionTemplate (not @Transactional) on purpose: the scheduler calls this
   * method on the same object, and a self-call bypasses Spring's @Transactional proxy.
   */
  public int relayBatch() {
    Integer published = tx.execute(status -> {
      List<Row> rows = jdbc.query("""
          select id, aggregate_id, event_type, payload::text, created_at
            from outbox
           where published_at is null
           order by created_at
           limit ?
             for update skip locked
          """,
          (rs, i) -> new Row(rs.getObject(1, UUID.class), rs.getObject(2, UUID.class), rs.getString(3),
              rs.getString(4), rs.getObject(5, OffsetDateTime.class)),
          batchSize);

      for (Row row : rows) {
        publish(row);
        failPoints.hit(FAIL_AFTER_PUBLISH_BEFORE_MARK);   // crash here → row stays unpublished → re-sent
        jdbc.update("update outbox set published_at = now() where id = ?", row.id());
      }
      return rows.size();
    });
    return published == null ? 0 : published;
  }

  private void publish(Row row) {
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

    try {
      kafka.send(record).get(10, TimeUnit.SECONDS);   // WAIT for the broker's ack before marking
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      throw new IllegalStateException("interrupted while publishing " + row.id(), e);
    } catch (Exception e) {
      // Throwing rolls back the batch: nothing is marked published, everything is retried next tick.
      throw new IllegalStateException("publish failed for outbox row " + row.id(), e);
    }
    log.info("published event {} ({}) for {}", row.id(), row.eventType(), row.aggregateId());
  }
}
