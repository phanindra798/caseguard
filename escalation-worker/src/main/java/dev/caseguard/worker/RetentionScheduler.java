package dev.caseguard.worker;

import java.time.Instant;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/** Runs the dedupe retention job on a timer. Tests switch it off and call {@link DedupeRetention}. */
@Component
@ConditionalOnProperty(name = "caseguard.retention.enabled", havingValue = "true", matchIfMissing = true)
public class RetentionScheduler {

  private static final Logger log = LoggerFactory.getLogger(RetentionScheduler.class);
  private final DedupeRetention dedupe;

  public RetentionScheduler(DedupeRetention dedupe) {
    this.dedupe = dedupe;
  }

  @Scheduled(initialDelayString = "${caseguard.retention.initial-delay:PT1M}",
             fixedDelayString = "${caseguard.retention.interval:PT1H}")
  public void run() {
    try {
      dedupe.purge(Instant.now());
    } catch (RuntimeException e) {
      log.warn("retention run failed, will retry next interval: {}", e.getMessage());
    }
  }
}
