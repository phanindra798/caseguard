package dev.caseguard.caseapi.outbox;

import java.time.Duration;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.common.config.TopicConfig;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

/** Creates the topic at startup if it doesn't exist (Spring's KafkaAdmin applies NewTopic beans). */
@Configuration
public class KafkaTopicsConfig {

  @Bean
  NewTopic escalationRequestedTopic(@Value("${caseguard.topics.escalation-requested}") String name,
                                    @Value("${caseguard.topics.retention:P7D}") Duration retention,
                                    @Value("${caseguard.topics.segment:P1D}") Duration segment) {
    // 3 partitions: room for 3 worker instances in parallel; ordering is kept per key (escalation id).
    // Retention is set EXPLICITLY (not left to the broker default) because the worker's dedupe
    // retention depends on it: see the worker's DedupeRetention.
    return TopicBuilder.name(name).partitions(3).replicas(1)
        .config(TopicConfig.RETENTION_MS_CONFIG, String.valueOf(retention.toMillis()))
        .config(TopicConfig.SEGMENT_MS_CONFIG, String.valueOf(segment.toMillis()))
        .build();
  }
}
