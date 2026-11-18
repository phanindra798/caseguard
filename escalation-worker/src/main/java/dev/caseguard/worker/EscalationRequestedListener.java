package dev.caseguard.worker;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;
import tools.jackson.databind.json.JsonMapper;

/**
 * Kafka entry point. The ORDER of the last two steps is the whole point:
 *
 * <pre>
 *   1. handler.process(event)   → DB transaction COMMITS
 *   2. ack.acknowledge()        → Kafka offset is committed
 * </pre>
 *
 * Crash between 1 and 2 → Kafka redelivers → step 1 sees the event in processed_events → skip.
 * Result: work done exactly once, even though the message arrived twice.
 * (The reverse order, ack first, could LOSE work on a crash. We prefer a harmless duplicate.)
 */
@Component
public class EscalationRequestedListener {

  public static final String FAIL_AFTER_DB_COMMIT_BEFORE_ACK = "WORKER_AFTER_DB_COMMIT_BEFORE_ACK";
  private static final Logger log = LoggerFactory.getLogger(EscalationRequestedListener.class);

  private final EscalationHandler handler;
  private final FailPoints failPoints;
  private final ProcessingStats stats;
  private final JsonMapper json;

  public EscalationRequestedListener(EscalationHandler handler, FailPoints failPoints,
                                     ProcessingStats stats, JsonMapper json) {
    this.handler = handler;
    this.failPoints = failPoints;
    this.stats = stats;
    this.json = json;
  }

  @KafkaListener(topics = "${caseguard.topics.escalation-requested}")
  public void onMessage(ConsumerRecord<String, String> record, Acknowledgment ack) {
    EscalationRequestedEvent ev = EscalationRequestedEvent.parse(record.value(), json);
    stats.delivered(ev.eventId());

    EscalationHandler.Outcome outcome = handler.process(ev);          // 1. DB commit
    if (outcome == EscalationHandler.Outcome.DUPLICATE) {
      stats.duplicateSkipped(ev.eventId());
      log.info("duplicate event {} skipped (escalation {})", ev.eventId(), ev.escalationId());
    } else {
      log.info("event {} processed: follow-up task created for escalation {}", ev.eventId(), ev.escalationId());
    }

    failPoints.hit(FAIL_AFTER_DB_COMMIT_BEFORE_ACK);                  // crash window
    ack.acknowledge();                                                // 2. offset commit
  }
}
