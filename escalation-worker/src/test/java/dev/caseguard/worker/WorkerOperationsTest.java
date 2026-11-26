package dev.caseguard.worker;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.apache.kafka.clients.admin.Admin;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.common.config.TopicConfig;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;

/**
 * Release 4.2: dedupe retention must never make exactly-once unsafe, and the worker's metrics must
 * count what really happened.
 */
class WorkerOperationsTest extends WorkerTestBase {

  private static final Duration WAIT = Duration.ofSeconds(25);

  @Autowired DedupeRetention retention;
  @Autowired TopicLifetime topicLifetime;
  @Autowired MeterRegistry registry;
  @Autowired PrometheusMeterRegistry prometheus;

  private UUID processedEvent(Duration ago) {
    UUID id = UUID.randomUUID();
    jdbc.update("insert into processed_events (event_id, processed_at) values (?, now() - make_interval(secs => ?))",
        id, ago.toSeconds());
    return id;
  }

  private boolean known(UUID eventId) {
    return count("select count(*) from processed_events where event_id = ?", eventId) == 1;
  }

  private String createTopic(Map<String, String> configs) throws Exception {
    String name = "retention-check-" + UUID.randomUUID();
    try (Admin admin = Admin.create(Map.of(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap))) {
      admin.createTopics(List.of(new NewTopic(name, 1, (short) 1).configs(configs))).all().get();
    }
    return name;
  }

  private double outcome(String outcome) {
    return registry.get("caseguard.worker.events").tag("outcome", outcome).counter().count();
  }

  // ------------------------------------------------------------------ retention

  @Test
  @DisplayName("topic created by the app: max message lifetime = retention 7d + segment 1d")
  void readsLifetimeFromBroker() {
    assertThat(topicLifetime.maxMessageLifetime(TOPIC)).contains(Duration.ofDays(8));
  }

  @Test
  @DisplayName("safe topic: dedupe rows older than 14 days are deleted, recent ones kept")
  void deletesOldDedupeRows() {
    UUID old1 = processedEvent(Duration.ofDays(20));
    UUID old2 = processedEvent(Duration.ofDays(15));
    UUID recent = processedEvent(Duration.ofDays(3));

    DedupeRetention.Run run = retention.purge(Instant.now());

    assertThat(run.result()).isEqualTo(DedupeRetention.Result.DELETED);
    assertThat(run.deleted()).isEqualTo(2);
    assertThat(known(old1)).isFalse();
    assertThat(known(old2)).isFalse();
    assertThat(known(recent)).isTrue();
  }

  @Test
  @DisplayName("after a purge, dedupe still works: a redelivered recent event is still skipped")
  void dedupeStillWorksAfterPurge() throws Exception {
    UUID esc = insertEscalation("C-1042");
    UUID eventId = UUID.randomUUID();
    String event = envelope(eventId, esc, "acme", "C-1042");
    publish(esc.toString(), event);
    await().atMost(WAIT).until(() -> count("select count(*) from follow_up_tasks where escalation_id = ?", esc) == 1);
    processedEvent(Duration.ofDays(30));

    assertThat(retention.purge(Instant.now()).deleted()).isEqualTo(1);   // only the 30-day-old row

    publish(esc.toString(), event);                                        // redelivery
    await().atMost(WAIT).until(() -> stats.duplicatesSkipped(eventId) == 1);
    assertThat(count("select count(*) from follow_up_tasks where escalation_id = ?", esc)).isEqualTo(1);
  }

  @Test
  @DisplayName("broker defaults (7d retention + 7d segment = 14d) vs 14d dedupe: UNSAFE, nothing deleted")
  void refusesWhenTopicCanOutliveDedupe() throws Exception {
    // Exactly the trap: "Kafka keeps 7 days, we keep 14, that's double" is wrong.
    String topic = createTopic(Map.of(
        TopicConfig.RETENTION_MS_CONFIG, String.valueOf(Duration.ofDays(7).toMillis()),
        TopicConfig.SEGMENT_MS_CONFIG, String.valueOf(Duration.ofDays(7).toMillis())));
    UUID old = processedEvent(Duration.ofDays(20));
    double skippedBefore = registry.counter("caseguard.retention.skipped",
        "table", "processed_events", "reason", "unsafe").count();

    DedupeRetention.Run run = retention.purge(topic, Instant.now());

    assertThat(run.result()).isEqualTo(DedupeRetention.Result.SKIPPED_UNSAFE);
    assertThat(run.detail()).contains("PT336H");                 // 14 days, as the broker reports it
    assertThat(known(old)).isTrue();
    assertThat(registry.counter("caseguard.retention.skipped",
        "table", "processed_events", "reason", "unsafe").count()).isEqualTo(skippedBefore + 1);
  }

  @Test
  @DisplayName("infinite retention (retention.ms = -1): messages can be replayed forever → never delete")
  void refusesWithInfiniteRetention() throws Exception {
    String topic = createTopic(Map.of(TopicConfig.RETENTION_MS_CONFIG, "-1"));
    UUID old = processedEvent(Duration.ofDays(365));

    assertThat(retention.purge(topic, Instant.now()).result()).isEqualTo(DedupeRetention.Result.SKIPPED_UNKNOWN);
    assertThat(known(old)).isTrue();
  }

  @Test
  @DisplayName("compacted topic: keys can live forever → never delete")
  void refusesWithCompaction() throws Exception {
    String topic = createTopic(Map.of(TopicConfig.CLEANUP_POLICY_CONFIG, TopicConfig.CLEANUP_POLICY_COMPACT));
    processedEvent(Duration.ofDays(365));

    assertThat(retention.purge(topic, Instant.now()).result()).isEqualTo(DedupeRetention.Result.SKIPPED_UNKNOWN);
    assertThat(count("select count(*) from processed_events")).isEqualTo(1);
  }

  @Test
  @DisplayName("topic that doesn't exist / can't be described: unknown = unsafe → never delete")
  void refusesWhenBrokerCantSay() {
    processedEvent(Duration.ofDays(365));

    assertThat(retention.purge("no-such-topic-" + UUID.randomUUID(), Instant.now()).result())
        .isEqualTo(DedupeRetention.Result.SKIPPED_UNKNOWN);
    assertThat(count("select count(*) from processed_events")).isEqualTo(1);
  }

  // ------------------------------------------------------------------ metrics

  @Test
  @DisplayName("metrics count processed, duplicate and dead-lettered events, and record the lag")
  void countsOutcomes() throws Exception {
    double processed = outcome("processed");
    double duplicate = outcome("duplicate");
    double dead = outcome("dead_lettered");
    long lagSamples = registry.get("caseguard.worker.event.lag").timer().count();

    UUID esc = insertEscalation("C-1042");
    UUID eventId = UUID.randomUUID();
    String event = envelope(eventId, esc, "acme", "C-1042");
    publish(esc.toString(), event);
    publish(esc.toString(), event);
    publish("poison", "{not json");

    await().atMost(WAIT).until(() -> outcome("dead_lettered") == dead + 1 && outcome("duplicate") == duplicate + 1);
    assertThat(outcome("processed")).isEqualTo(processed + 1);
    assertThat(registry.get("caseguard.worker.event.lag").timer().count()).isEqualTo(lagSamples + 1);
  }

  @Test
  @DisplayName("Prometheus scrape contains the worker metrics and Kafka's own consumer lag")
  void prometheusScrape() throws Exception {
    UUID esc = insertEscalation("C-1042");
    publish(esc.toString(), envelope(UUID.randomUUID(), esc, "acme", "C-1042"));
    await().atMost(WAIT).until(() -> count("select count(*) from follow_up_tasks where escalation_id = ?", esc) == 1);

    String scrape = prometheus.scrape();
    assertThat(scrape)
        .contains("caseguard_worker_events_total{application=\"escalation-worker\",outcome=\"processed\"}")
        .contains("caseguard_worker_event_lag_seconds_bucket")
        .contains("caseguard_worker_processing_seconds_count")
        .contains("caseguard_retention_deleted_total")
        .contains("kafka_consumer_fetch_manager_records_lag_max");
  }
}
