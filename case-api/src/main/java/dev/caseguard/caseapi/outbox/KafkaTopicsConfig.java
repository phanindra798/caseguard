package dev.caseguard.caseapi.outbox;

import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;

/** Creates the topic at startup if it doesn't exist (Spring's KafkaAdmin applies NewTopic beans). */
@Configuration
public class KafkaTopicsConfig {

  @Bean
  NewTopic escalationRequestedTopic(@Value("${caseguard.topics.escalation-requested}") String name) {
    // 3 partitions: room for 3 worker instances in parallel; ordering is kept per key (escalation id).
    return TopicBuilder.name(name).partitions(3).replicas(1).build();
  }
}
