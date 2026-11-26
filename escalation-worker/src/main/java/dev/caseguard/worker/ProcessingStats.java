package dev.caseguard.worker;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.stereotype.Component;

/**
 * In-memory counters per event: how often it was delivered, and how often it was skipped as a
 * duplicate. Tests use them to PROVE a redelivery happened (not just that the end state is right).
 * Operational counts for dashboards and alerts are in {@link WorkerMetrics} (Micrometer); these
 * per-event counters exist for tests.
 */
@Component
public class ProcessingStats {

  private final Map<UUID, AtomicInteger> deliveries = new ConcurrentHashMap<>();
  private final Map<UUID, AtomicInteger> duplicates = new ConcurrentHashMap<>();

  void delivered(UUID eventId) {
    deliveries.computeIfAbsent(eventId, k -> new AtomicInteger()).incrementAndGet();
  }

  void duplicateSkipped(UUID eventId) {
    duplicates.computeIfAbsent(eventId, k -> new AtomicInteger()).incrementAndGet();
  }

  public int deliveries(UUID eventId) {
    AtomicInteger n = deliveries.get(eventId);
    return n == null ? 0 : n.get();
  }

  public int duplicatesSkipped(UUID eventId) {
    AtomicInteger n = duplicates.get(eventId);
    return n == null ? 0 : n.get();
  }
}
