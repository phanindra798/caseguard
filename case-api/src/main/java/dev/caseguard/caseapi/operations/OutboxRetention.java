package dev.caseguard.caseapi.operations;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import java.time.Duration;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * Deletes outbox rows that were PUBLISHED long ago. Unpublished rows are never touched: they are
 * events still owed to Kafka.
 *
 * <p>Why deleting is safe: a published row has been acknowledged by the broker, so the relay will
 * never send it again. The business record (the escalation) stays; only the delivery bookkeeping
 * goes.
 *
 * <p>How: many SMALL deletes, each its own short transaction, instead of one huge
 * {@code DELETE ... WHERE published_at < cutoff}. A huge delete holds locks for a long time,
 * writes a burst of WAL, and is all-or-nothing on failure. {@code FOR UPDATE SKIP LOCKED} lets two
 * Case API instances run this job at the same time without blocking each other.
 */
@Component
public class OutboxRetention {

  private static final Logger log = LoggerFactory.getLogger(OutboxRetention.class);

  static final String DELETE_BATCH = """
      delete from outbox
       where id in (select id from outbox
                     where published_at is not null and published_at < ?
                     order by published_at
                     limit ?
                       for update skip locked)
      """;

  private final JdbcTemplate jdbc;
  private final Duration keep;
  private final int batchSize;
  private final int maxBatches;
  private final Counter deleted;

  public OutboxRetention(JdbcTemplate jdbc, MeterRegistry registry,
                         @Value("${caseguard.retention.outbox-published:P7D}") Duration keep,
                         @Value("${caseguard.retention.batch-size:1000}") int batchSize,
                         @Value("${caseguard.retention.max-batches-per-run:100}") int maxBatches) {
    this.jdbc = jdbc;
    this.keep = keep;
    this.batchSize = batchSize;
    this.maxBatches = maxBatches;
    this.deleted = Counter.builder("caseguard.retention.deleted")
        .description("Rows deleted by retention jobs")
        .tag("table", "outbox")
        .register(registry);
  }

  /** One run. Returns the number of rows deleted. */
  public int purge(Instant now) {
    OffsetDateTime cutoff = now.minus(keep).atOffset(ZoneOffset.UTC);
    int total = 0;
    for (int batch = 0; batch < maxBatches; batch++) {
      int n = jdbc.update(DELETE_BATCH, cutoff, batchSize);   // auto-commit: one short transaction each
      total += n;
      if (n < batchSize) {
        break;   // fewer than a full batch → nothing older is left
      }
    }
    deleted.increment(total);
    if (total > 0) {
      log.info("retention: deleted {} published outbox row(s) older than {}", total, keep);
    }
    return total;
  }
}
