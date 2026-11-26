package dev.caseguard.caseapi;

import static org.assertj.core.api.Assertions.assertThat;

import dev.caseguard.caseapi.operations.OutboxRetention;
import dev.caseguard.caseapi.outbox.OutboxMetrics;
import dev.caseguard.caseapi.outbox.OutboxRelay;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import java.net.URI;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * Release 4.2: the operational side of the outbox. Retention must delete ONLY old published rows,
 * and the metrics must tell the truth about backlog and delay.
 */
class OperationsTest extends ApiTestBase {

  @Autowired OutboxRetention retention;
  @Autowired OutboxMetrics outboxMetrics;
  @Autowired OutboxRelay relay;
  @Autowired MeterRegistry registry;

  /** An outbox row created {@code createdAgo} ago; published {@code publishedAgo} ago, or never (null). */
  private UUID outboxRow(Duration createdAgo, Duration publishedAgo) {
    UUID id = UUID.randomUUID();
    jdbc.update("""
        insert into outbox (id, tenant_id, aggregate_id, event_type, payload, created_at, published_at)
        values (?, 'acme', ?, 'EscalationRequested', '{}'::jsonb,
                now() - make_interval(secs => ?),
                case when ? then now() - make_interval(secs => ?) end)
        """, id, UUID.randomUUID(), createdAgo.toSeconds(),
        publishedAgo != null, publishedAgo == null ? 0 : publishedAgo.toSeconds());
    return id;
  }

  private boolean exists(UUID id) {
    return count("select count(*) from outbox where id = ?", id) == 1;
  }

  @Test
  @DisplayName("retention deletes old PUBLISHED rows only; recent and unpublished rows stay")
  void retentionDeletesOnlyOldPublishedRows() {
    UUID oldPublished = outboxRow(Duration.ofDays(10), Duration.ofDays(9));
    UUID recentPublished = outboxRow(Duration.ofDays(2), Duration.ofDays(1));
    UUID oldUnpublished = outboxRow(Duration.ofDays(30), null);   // still owed to Kafka: never deleted

    int deleted = retention.purge(Instant.now());

    assertThat(deleted).isEqualTo(1);
    assertThat(exists(oldPublished)).isFalse();
    assertThat(exists(recentPublished)).isTrue();
    assertThat(exists(oldUnpublished)).isTrue();
  }

  @Test
  @DisplayName("retention deletes in small batches until nothing old is left")
  void retentionWorksInBatches() {
    for (int i = 0; i < 7; i++) {
      outboxRow(Duration.ofDays(20), Duration.ofDays(20));
    }
    UUID keep = outboxRow(Duration.ofHours(1), Duration.ofHours(1));
    // Batch size 2 → 4 statements (2+2+2+1); the short last batch tells the loop it is done.
    OutboxRetention small = new OutboxRetention(jdbc, new SimpleMeterRegistry(), Duration.ofDays(7), 2, 100);

    assertThat(small.purge(Instant.now())).isEqualTo(7);
    assertThat(count("select count(*) from outbox")).isEqualTo(1);
    assertThat(exists(keep)).isTrue();
    assertThat(small.purge(Instant.now())).isZero();     // running again is harmless
  }

  @Test
  @DisplayName("max batches per run bounds one run's work; the next run continues")
  void retentionIsBoundedPerRun() {
    for (int i = 0; i < 5; i++) {
      outboxRow(Duration.ofDays(20), Duration.ofDays(20));
    }
    OutboxRetention bounded = new OutboxRetention(jdbc, new SimpleMeterRegistry(), Duration.ofDays(7), 2, 1);

    assertThat(bounded.purge(Instant.now())).isEqualTo(2);
    assertThat(bounded.purge(Instant.now())).isEqualTo(2);
    assertThat(bounded.purge(Instant.now())).isEqualTo(1);
  }

  @Test
  @DisplayName("backlog and oldest-age gauges report unpublished events, and drop to 0 after the relay runs")
  void outboxGaugesTellTheTruth() {
    outboxRow(Duration.ofSeconds(120), null);
    outboxRow(Duration.ofSeconds(5), null);
    outboxRow(Duration.ofDays(3), Duration.ofDays(3));   // published: not part of the backlog

    assertThat(registry.get("caseguard.outbox.backlog").gauge().value()).isEqualTo(2);
    assertThat(registry.get("caseguard.outbox.oldest.unpublished.age").gauge().value())
        .isBetween(119.0, 180.0);

    double publishedBefore = registry.get("caseguard.outbox.published").counter().count();
    assertThat(relay.relayBatch()).isEqualTo(2);

    assertThat(outboxMetrics.backlog()).isZero();
    assertThat(outboxMetrics.oldestUnpublishedAgeSeconds()).isZero();
    assertThat(registry.get("caseguard.outbox.published").counter().count()).isEqualTo(publishedBefore + 2);
    assertThat(registry.get("caseguard.outbox.relay.batch").timer().count()).isPositive();
  }

  @Test
  @DisplayName("an empty relay poll is not counted as a batch")
  void emptyPollsAreNotTimed() {
    long before = registry.get("caseguard.outbox.relay.batch").timer().count();
    assertThat(relay.relayBatch()).isZero();
    assertThat(registry.get("caseguard.outbox.relay.batch").timer().count()).isEqualTo(before);
  }

  @Test
  @DisplayName("/actuator/prometheus serves the CaseGuard metrics in Prometheus format")
  void prometheusEndpoint() throws Exception {
    outboxRow(Duration.ofSeconds(30), null);
    HttpResponse<String> r = http.send(
        HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/actuator/prometheus")).GET().build(),
        HttpResponse.BodyHandlers.ofString());

    assertThat(r.statusCode()).isEqualTo(200);
    assertThat(r.body())
        .contains("caseguard_outbox_backlog_events{application=\"case-api\"} 1.0")
        .contains("caseguard_outbox_oldest_unpublished_age_seconds")
        .contains("caseguard_outbox_published_events_total")
        .contains("caseguard_retention_deleted_total{application=\"case-api\",table=\"outbox\"}");
  }
}
