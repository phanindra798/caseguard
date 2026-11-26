package dev.caseguard.worker;

import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.TimeUnit;
import org.apache.kafka.clients.admin.Admin;
import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.Config;
import org.apache.kafka.common.config.ConfigResource;
import org.apache.kafka.common.config.TopicConfig;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.stereotype.Component;

/**
 * Asks the BROKER (not our own config file) how long a message can live on a topic.
 *
 * <p>Kafka retention is a MINIMUM, not a maximum: a message is deleted only when its whole log
 * segment is older than {@code retention.ms}, and the active segment is closed only after
 * {@code segment.ms}. So the longest a message can survive is about
 * {@code retention.ms + segment.ms}. With the broker defaults (7 days + 7 days) that is two weeks,
 * not one.
 *
 * <p>Returns empty when there is no upper bound (infinite retention, a compacted topic) or when the
 * broker can't be asked. Callers must treat empty as "not safe".
 */
@Component
public class TopicLifetime {

  private final KafkaAdmin kafkaAdmin;

  public TopicLifetime(KafkaAdmin kafkaAdmin) {
    this.kafkaAdmin = kafkaAdmin;
  }

  public Optional<Duration> maxMessageLifetime(String topic) {
    Map<String, Object> props = new java.util.HashMap<>(kafkaAdmin.getConfigurationProperties());
    props.putIfAbsent(AdminClientConfig.REQUEST_TIMEOUT_MS_CONFIG, 10_000);
    props.putIfAbsent(AdminClientConfig.DEFAULT_API_TIMEOUT_MS_CONFIG, 10_000);
    ConfigResource resource = new ConfigResource(ConfigResource.Type.TOPIC, topic);
    try (Admin admin = Admin.create(props)) {
      Config config = admin.describeConfigs(List.of(resource)).all().get(15, TimeUnit.SECONDS).get(resource);
      String cleanup = config.get(TopicConfig.CLEANUP_POLICY_CONFIG).value();
      long retentionMs = Long.parseLong(config.get(TopicConfig.RETENTION_MS_CONFIG).value());
      long segmentMs = Long.parseLong(config.get(TopicConfig.SEGMENT_MS_CONFIG).value());
      if (cleanup.contains(TopicConfig.CLEANUP_POLICY_COMPACT) || retentionMs < 0) {
        return Optional.empty();   // messages may live forever
      }
      return Optional.of(Duration.ofMillis(retentionMs + segmentMs));
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      return Optional.empty();
    } catch (Exception e) {
      return Optional.empty();     // broker unreachable, unknown topic, ...: unknown = unsafe
    }
  }
}
