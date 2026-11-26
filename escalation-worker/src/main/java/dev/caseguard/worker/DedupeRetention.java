package dev.caseguard.worker;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Optional;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * Deletes old rows from {@code processed_events}, the worker's dedupe table, WITHOUT ever making
 * exactly-once unsafe.
 *
 * <p>The danger: a dedupe row is the only thing that recognises a redelivered event. Delete it
 * while Kafka can still deliver that event again (a consumer-group reset to "earliest", a replay
 * after an incident) and the event is processed twice.
 *
 * <p>The rule: a dedupe row may go only when no copy of its event can still exist in Kafka, i.e.
 * when it is older than the topic's MAXIMUM message lifetime (see {@link TopicLifetime}) plus a
 * safety margin. The job checks this against the broker on EVERY run, because topic settings can
 * be changed on the broker without anyone touching our config. If the rule fails, or the broker
 * can't be asked, the job deletes nothing: a growing table is a cost, a duplicate is a bug.
 *
 * <p>Backstop if all else failed: {@code follow_up_tasks} has a UNIQUE constraint per escalation, so
 * a late duplicate would fail loudly (and land in the dead-letter topic), not create a second task.
 */
@Component
public class DedupeRetention {

  public enum Result { DELETED, SKIPPED_UNSAFE, SKIPPED_UNKNOWN }

  public record Run(Result result, int deleted, String detail) {}

  private static final Logger log = LoggerFactory.getLogger(DedupeRetention.class);

  static final String DELETE_BATCH = """
      delete from processed_events
       where event_id in (select event_id from processed_events
                           where processed_at < ?
                           order by processed_at
                           limit ?
                             for update skip locked)
      """;

  private final JdbcTemplate jdbc;
  private final TopicLifetime topicLifetime;
  private final MeterRegistry registry;
  private final String topic;
  private final Duration keep;
  private final Duration margin;
  private final int batchSize;
  private final int maxBatches;
  private final Counter deleted;

  public DedupeRetention(JdbcTemplate jdbc, TopicLifetime topicLifetime, MeterRegistry registry,
                         @Value("${caseguard.topics.escalation-requested}") String topic,
                         @Value("${caseguard.retention.processed-events:P14D}") Duration keep,
                         @Value("${caseguard.retention.safety-margin:P1D}") Duration margin,
                         @Value("${caseguard.retention.batch-size:1000}") int batchSize,
                         @Value("${caseguard.retention.max-batches-per-run:100}") int maxBatches) {
    this.jdbc = jdbc;
    this.topicLifetime = topicLifetime;
    this.registry = registry;
    this.topic = topic;
    this.keep = keep;
    this.margin = margin;
    this.batchSize = batchSize;
    this.maxBatches = maxBatches;
    this.deleted = Counter.builder("caseguard.retention.deleted")
        .description("Rows deleted by retention jobs")
        .tag("table", "processed_events")
        .register(registry);
  }

  public Run purge(Instant now) {
    return purge(topic, now);
  }

  /** One run against the given topic's settings (tests point it at topics with other settings). */
  public Run purge(String topicName, Instant now) {
    Optional<Duration> lifetime = topicLifetime.maxMessageLifetime(topicName);
    if (lifetime.isEmpty()) {
      return skip(Result.SKIPPED_UNKNOWN, "unknown",
          "topic " + topicName + " has no known upper bound on message lifetime (infinite retention, "
              + "compaction, or broker unreachable)");
    }
    Duration required = lifetime.get().plus(margin);
    if (keep.compareTo(required) <= 0) {
      return skip(Result.SKIPPED_UNSAFE, "unsafe",
          "dedupe retention " + keep + " must be longer than the topic's max message lifetime "
              + lifetime.get() + " + margin " + margin);
    }

    OffsetDateTime cutoff = now.minus(keep).atOffset(ZoneOffset.UTC);
    int total = 0;
    for (int batch = 0; batch < maxBatches; batch++) {
      int n = jdbc.update(DELETE_BATCH, cutoff, batchSize);
      total += n;
      if (n < batchSize) {
        break;
      }
    }
    deleted.increment(total);
    if (total > 0) {
      log.info("retention: deleted {} processed_events row(s) older than {}", total, keep);
    }
    return new Run(Result.DELETED, total, "max message lifetime " + lifetime.get());
  }

  private Run skip(Result result, String reason, String detail) {
    registry.counter("caseguard.retention.skipped", "table", "processed_events", "reason", reason).increment();
    log.warn("retention: NOT deleting dedupe rows: {}", detail);
    return new Run(result, 0, detail);
  }
}
