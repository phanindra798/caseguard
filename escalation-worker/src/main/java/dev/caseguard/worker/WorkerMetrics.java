package dev.caseguard.worker;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import io.micrometer.core.instrument.Timer;
import java.time.Duration;
import java.time.Instant;
import org.springframework.stereotype.Component;

/**
 * The worker's own metrics. Kafka's consumer lag (records-lag-max) comes for free from the Kafka
 * client metrics that Spring Kafka registers with Micrometer.
 *
 * <ul>
 *   <li>{@code caseguard_worker_events_total{outcome}}: processed | duplicate | dead_lettered.
 *       A burst of "duplicate" means redeliveries (a crash, a rebalance); any "dead_lettered" needs
 *       a human.</li>
 *   <li>{@code caseguard_worker_delivery_failures_total}: attempts that threw and will be retried
 *       (DB blips). Retries are fine; a steady stream is not.</li>
 *   <li>{@code caseguard_worker_processing_seconds}: time in the handler (one DB transaction).</li>
 *   <li>{@code caseguard_worker_event_lag_seconds}: from the event being written to the outbox to
 *       the worker finishing it, i.e. the user-visible delay of the whole pipeline.</li>
 * </ul>
 */
@Component
public class WorkerMetrics {

  private final Counter processed;
  private final Counter duplicate;
  private final Counter deadLettered;
  private final Counter deliveryFailures;
  private final Timer processing;
  private final Timer eventLag;

  public WorkerMetrics(MeterRegistry registry) {
    this.processed = outcome(registry, "processed");
    this.duplicate = outcome(registry, "duplicate");
    this.deadLettered = outcome(registry, "dead_lettered");
    this.deliveryFailures = Counter.builder("caseguard.worker.delivery.failures")
        .description("Delivery attempts that threw (then retried, or dead-lettered if not retryable)")
        .register(registry);
    this.processing = Timer.builder("caseguard.worker.processing")
        .description("Time spent handling one event (one database transaction)")
        .publishPercentileHistogram()
        .register(registry);
    this.eventLag = Timer.builder("caseguard.worker.event.lag")
        .description("From the event being written to the outbox to the worker completing it")
        .publishPercentileHistogram()
        .minimumExpectedValue(Duration.ofMillis(10))
        .maximumExpectedValue(Duration.ofMinutes(10))
        .register(registry);
  }

  private static Counter outcome(MeterRegistry registry, String outcome) {
    return Counter.builder("caseguard.worker.events")
        .description("Events handled by the worker, by outcome")
        .tag("outcome", outcome)
        .register(registry);
  }

  void processed(Duration handlerTime, Instant occurredAt) {
    processed.increment();
    processing.record(handlerTime);
    if (occurredAt != null) {
      // Both clocks are database/JVM clocks on hosts kept in sync by NTP; a negative value means skew.
      Duration lag = Duration.between(occurredAt, Instant.now());
      if (!lag.isNegative()) {
        eventLag.record(lag);
      }
    }
  }

  void duplicate(Duration handlerTime) {
    duplicate.increment();
    processing.record(handlerTime);
  }

  void deadLettered() {
    deadLettered.increment();
  }

  void deliveryFailed() {
    deliveryFailures.increment();
  }
}
