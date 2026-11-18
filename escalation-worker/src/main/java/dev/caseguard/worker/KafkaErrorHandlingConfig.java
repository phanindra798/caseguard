package dev.caseguard.worker;

import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.common.TopicPartition;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.FixedBackOff;

/**
 * What happens when processing a message throws:
 *
 * <ul>
 *   <li>Temporary problem (DB blip, our THROW fail points): retry the SAME message up to 4 times,
 *       500 ms apart. The consumer seeks back to it; offsets after it are not committed.</li>
 *   <li>Poison message ({@link InvalidEventException}): never retry. Publish it to the
 *       dead-letter topic {@code <topic>.DLT} for a human to inspect, then move on, so one bad
 *       message can't block the whole partition.</li>
 * </ul>
 */
@Configuration
public class KafkaErrorHandlingConfig {

  @Bean
  DefaultErrorHandler errorHandler(KafkaTemplate<Object, Object> template) {
    // Sends to "<original topic>.DLT", same partition number. Set explicitly instead of relying on
    // the library default, which changed between Spring Kafka versions (".DLT" → "-dlt").
    DeadLetterPublishingRecoverer toDeadLetter = new DeadLetterPublishingRecoverer(template,
        (record, ex) -> new TopicPartition(record.topic() + ".DLT", record.partition()));
    DefaultErrorHandler handler = new DefaultErrorHandler(toDeadLetter, new FixedBackOff(500L, 4L));
    handler.addNotRetryableExceptions(InvalidEventException.class);
    return handler;
  }

  @Bean
  NewTopic escalationRequestedTopic(@Value("${caseguard.topics.escalation-requested}") String name) {
    return TopicBuilder.name(name).partitions(3).replicas(1).build();
  }

  @Bean
  NewTopic escalationRequestedDeadLetterTopic(@Value("${caseguard.topics.escalation-requested}") String name) {
    return TopicBuilder.name(name + ".DLT").partitions(3).replicas(1).build();
  }
}
