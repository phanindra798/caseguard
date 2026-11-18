package dev.caseguard.caseapi.outbox;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Runs the relay every few hundred milliseconds. Separate from {@link OutboxRelay} so tests can
 * switch the schedule off ({@code caseguard.relay.enabled=false}) and call the relay directly,
 * which makes them deterministic.
 */
@Component
@ConditionalOnProperty(name = "caseguard.relay.enabled", havingValue = "true", matchIfMissing = true)
public class OutboxRelayScheduler {

  private static final Logger log = LoggerFactory.getLogger(OutboxRelayScheduler.class);
  private final OutboxRelay relay;

  public OutboxRelayScheduler(OutboxRelay relay) {
    this.relay = relay;
  }

  @Scheduled(fixedDelayString = "${caseguard.relay.interval-ms:200}")
  public void tick() {
    try {
      relay.relayBatch();
    } catch (RuntimeException e) {
      // Broker down, etc.: the batch rolled back; the next tick retries. Never kill the scheduler.
      log.warn("outbox relay tick failed, will retry: {}", e.getMessage());
    }
  }
}
