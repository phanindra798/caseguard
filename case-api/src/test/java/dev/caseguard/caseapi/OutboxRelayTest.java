package dev.caseguard.caseapi;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.caseguard.caseapi.outbox.OutboxRelay;
import dev.caseguard.caseapi.reliability.FailPoints;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import tools.jackson.databind.JsonNode;

/**
 * Phase 1.3: the outbox relay against a REAL Kafka broker.
 * The relay schedule is off in tests; each test calls relayBatch() itself.
 */
class OutboxRelayTest extends ApiTestBase {

  @Autowired OutboxRelay relay;
  @Value("${spring.kafka.bootstrap-servers}") String bootstrap;
  @Value("${caseguard.topics.escalation-requested}") String topic;

  /** Read every message on the topic whose key is this escalation id. */
  private List<ConsumerRecord<String, String>> messagesFor(String escalationId) {
    Map<String, Object> props = Map.of(
        ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap,
        ConsumerConfig.GROUP_ID_CONFIG, "test-reader-" + UUID.randomUUID(),
        ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class,
        ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
    List<ConsumerRecord<String, String>> found = new ArrayList<>();
    try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(props)) {
      List<TopicPartition> partitions = consumer.partitionsFor(topic).stream()
          .map(p -> new TopicPartition(topic, p.partition())).toList();
      consumer.assign(partitions);
      consumer.seekToBeginning(partitions);
      Map<TopicPartition, Long> end = consumer.endOffsets(partitions);
      long deadline = System.currentTimeMillis() + 10_000;
      while (System.currentTimeMillis() < deadline
          && partitions.stream().anyMatch(p -> consumer.position(p) < end.get(p))) {
        for (ConsumerRecord<String, String> r : consumer.poll(Duration.ofMillis(200))) {
          if (escalationId.equals(r.key())) {
            found.add(r);
          }
        }
      }
    }
    return found;
  }

  private String header(ConsumerRecord<String, String> r, String name) {
    return new String(r.headers().lastHeader(name).value(), StandardCharsets.UTF_8);
  }

  private String newEscalation() throws Exception {
    return body(escalate("acme", "cust-A1", "C-1042", UUID.randomUUID())).get("id").asString();
  }

  @Test
  @DisplayName("relay publishes the event with its id, then marks the row published")
  void publishesAndMarks() throws Exception {
    String escalationId = newEscalation();
    String outboxId = jdbc.queryForObject("select id::text from outbox", String.class);

    assertThat(relay.relayBatch()).isEqualTo(1);
    assertThat(count("select count(*) from outbox where published_at is not null")).isEqualTo(1);
    assertThat(relay.relayBatch()).as("nothing left to publish").isZero();

    List<ConsumerRecord<String, String>> msgs = messagesFor(escalationId);
    assertThat(msgs).hasSize(1);
    ConsumerRecord<String, String> m = msgs.get(0);
    assertThat(header(m, "event-id")).isEqualTo(outboxId);
    JsonNode envelope = json.readTree(m.value());
    assertThat(envelope.get("eventId").asString()).isEqualTo(outboxId);
    assertThat(envelope.get("eventType").asString()).isEqualTo("EscalationRequested");
    assertThat(envelope.get("payload").get("caseId").asString()).isEqualTo("C-1042");
  }

  @Test
  @DisplayName("crash after publish, before mark: re-sent with the SAME event id (at-least-once)")
  void crashAfterPublishCausesDuplicateWithSameEventId() throws Exception {
    String escalationId = newEscalation();
    String outboxId = jdbc.queryForObject("select id::text from outbox", String.class);

    failPoints.arm(OutboxRelay.FAIL_AFTER_PUBLISH_BEFORE_MARK, FailPoints.Action.THROW);
    assertThatThrownBy(() -> relay.relayBatch()).isInstanceOf(FailPoints.FailPointException.class);
    // The message reached Kafka, but the "published" mark was rolled back.
    assertThat(count("select count(*) from outbox where published_at is null")).isEqualTo(1);

    failPoints.disarm();                         // "restart"
    assertThat(relay.relayBatch()).isEqualTo(1);

    List<ConsumerRecord<String, String>> msgs = messagesFor(escalationId);
    assertThat(msgs).as("delivered twice").hasSize(2);
    assertThat(msgs).allSatisfy(m -> assertThat(header(m, "event-id")).isEqualTo(outboxId));
  }

  @Test
  @DisplayName("two relays at once never publish the same row twice (FOR UPDATE SKIP LOCKED)")
  void concurrentRelaysDoNotDoublePublish() throws Exception {
    List<String> ids = new ArrayList<>();
    for (String caseId : List.of("C-1042", "C-1043", "C-1050")) {   // cheap orders: no approval needed
      ids.add(body(escalate("acme", "cust-A1", caseId, UUID.randomUUID())).get("id").asString());
    }

    ExecutorService pool = Executors.newFixedThreadPool(4);
    CountDownLatch start = new CountDownLatch(1);
    List<Future<Integer>> results = new ArrayList<>();
    for (int i = 0; i < 4; i++) {
      results.add(pool.submit(() -> {
        start.await();
        return relay.relayBatch();
      }));
    }
    start.countDown();
    int total = 0;
    for (Future<Integer> f : results) {
      total += f.get();
    }
    pool.shutdown();

    assertThat(total).as("each row published by exactly one relay").isEqualTo(3);
    for (String id : ids) {
      assertThat(messagesFor(id)).hasSize(1);
    }
  }
}
