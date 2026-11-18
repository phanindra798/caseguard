package dev.caseguard.worker;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import javax.sql.DataSource;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.flywaydb.core.Flyway;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.test.context.ActiveProfiles;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;

/**
 * Real PostgreSQL + real Kafka. Each test RUN gets its own fresh topic, so leftover messages
 * from earlier runs (or from the Case API's tests) can never leak in.
 */
@SpringBootTest
@ActiveProfiles("test")
@Import(WorkerTestBase.SchemaFromCaseApiMigrations.class)
public abstract class WorkerTestBase {

  static final String TOPIC = "escalation.requested.test-" + UUID.randomUUID();

  @DynamicPropertySource
  static void topic(DynamicPropertyRegistry registry) {
    registry.add("caseguard.topics.escalation-requested", () -> TOPIC);
  }

  /** Build the schema from the Case API's OWN migration files: one source of truth. */
  @TestConfiguration
  static class SchemaFromCaseApiMigrations {
    @Bean
    Flyway testSchema(DataSource dataSource) {
      Flyway flyway = Flyway.configure()
          .dataSource(dataSource)
          .locations("filesystem:../case-api/src/main/resources/db/migration",
                     "filesystem:../case-api/src/main/resources/db/seed")
          .cleanDisabled(false)
          .load();
      flyway.clean();
      flyway.migrate();
      return flyway;
    }
  }

  @Autowired protected JdbcTemplate jdbc;
  @Autowired protected KafkaTemplate<String, String> kafka;
  @Autowired protected FailPoints failPoints;
  @Autowired protected ProcessingStats stats;
  @Value("${spring.kafka.bootstrap-servers}") protected String bootstrap;

  @BeforeEach
  void resetData() {
    jdbc.update("delete from follow_up_tasks");
    jdbc.update("delete from processed_events");
    jdbc.update("delete from escalations");
  }

  @AfterEach
  void disarm() {
    failPoints.disarm();
  }

  /** An escalation in REQUESTED state, as the Case API would have created it. */
  protected UUID insertEscalation(String caseId) {
    UUID id = UUID.randomUUID();
    jdbc.update("""
        insert into escalations (id, tenant_id, customer_id, case_id, operation_id, status, reason, policy_ref)
        values (?, 'acme', 'cust-A1', ?, ?, 'REQUESTED', 'late order', 'ESC-3@v2')
        """, id, caseId, UUID.randomUUID());
    return id;
  }

  /** The envelope exactly as the Case API's outbox relay builds it. */
  protected String envelope(UUID eventId, UUID escalationId, String tenantId, String caseId) {
    return """
        {"eventId":"%s","eventType":"EscalationRequested","occurredAt":"2026-11-18T10:00:00Z",
         "payload":{"escalationId":"%s","operationId":"%s","tenantId":"%s","customerId":"cust-A1",
                    "caseId":"%s","policyRef":"ESC-3@v2","requestedAt":"2026-11-18T10:00:00Z"}}
        """.formatted(eventId, escalationId, UUID.randomUUID(), tenantId, caseId);
  }

  protected void publish(String key, String value) throws Exception {
    kafka.send(TOPIC, key, value).get();
  }

  protected int count(String sql, Object... args) {
    return jdbc.queryForObject(sql, Integer.class, args);
  }

  /** All messages currently on a topic (used to inspect the dead-letter topic). */
  protected List<ConsumerRecord<String, String>> readAll(String topic) {
    Map<String, Object> props = Map.of(
        ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrap,
        ConsumerConfig.GROUP_ID_CONFIG, "test-reader-" + UUID.randomUUID(),
        ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class,
        ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG, StringDeserializer.class);
    List<ConsumerRecord<String, String>> out = new ArrayList<>();
    try (KafkaConsumer<String, String> consumer = new KafkaConsumer<>(props)) {
      List<TopicPartition> parts = consumer.partitionsFor(topic).stream()
          .map(p -> new TopicPartition(topic, p.partition())).toList();
      consumer.assign(parts);
      consumer.seekToBeginning(parts);
      Map<TopicPartition, Long> end = consumer.endOffsets(parts);
      long deadline = System.currentTimeMillis() + 10_000;
      while (System.currentTimeMillis() < deadline && parts.stream().anyMatch(p -> consumer.position(p) < end.get(p))) {
        consumer.poll(Duration.ofMillis(200)).forEach(out::add);
      }
    }
    return out;
  }
}
