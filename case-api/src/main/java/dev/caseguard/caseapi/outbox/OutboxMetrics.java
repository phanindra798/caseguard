package dev.caseguard.caseapi.outbox;

import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.binder.MeterBinder;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

/**
 * The two numbers that say whether the outbox is healthy, read from the database when
 * Prometheus scrapes:
 *
 * <ul>
 *   <li>{@code caseguard_outbox_backlog}: unpublished rows. Up and down is normal under load.</li>
 *   <li>{@code caseguard_outbox_oldest_unpublished_age_seconds}: how long the OLDEST unpublished
 *       row has waited. This is the alert signal: if the relay or Kafka is stuck, the backlog can
 *       stay small while this number keeps growing.</li>
 * </ul>
 *
 * Both queries use the partial index {@code outbox_unpublished_idx}, which only holds unpublished
 * rows, so they stay cheap however large the published history gets. If the database is down the
 * gauges report NaN ("no data") instead of a misleading 0.
 */
@Component
public class OutboxMetrics implements MeterBinder {

  private static final Logger log = LoggerFactory.getLogger(OutboxMetrics.class);
  private final JdbcTemplate jdbc;

  public OutboxMetrics(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  @Override
  public void bindTo(MeterRegistry registry) {
    Gauge.builder("caseguard.outbox.backlog", this, OutboxMetrics::backlog)
        .description("Outbox rows not yet published to Kafka")
        .baseUnit("events")
        .register(registry);
    Gauge.builder("caseguard.outbox.oldest.unpublished.age", this, OutboxMetrics::oldestUnpublishedAgeSeconds)
        .description("Age of the oldest outbox row not yet published (0 when the outbox is empty)")
        .baseUnit("seconds")
        .register(registry);
  }

  public double backlog() {
    return query("select count(*) from outbox where published_at is null");
  }

  public double oldestUnpublishedAgeSeconds() {
    return query("""
        select coalesce(extract(epoch from now() - min(created_at)), 0)
          from outbox where published_at is null
        """);
  }

  private double query(String sql) {
    try {
      Number n = jdbc.queryForObject(sql, Number.class);
      return n == null ? 0 : n.doubleValue();
    } catch (RuntimeException e) {
      log.debug("outbox metric unavailable: {}", e.getMessage());
      return Double.NaN;
    }
  }
}
