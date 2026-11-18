package dev.caseguard.worker;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;

import java.time.Duration;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * Phase 1.3: the worker creates exactly ONE follow-up task per escalation, whatever Kafka does.
 */
class EscalationWorkerTest extends WorkerTestBase {

  private static final Duration WAIT = Duration.ofSeconds(25);

  private int tasksFor(UUID escalationId) {
    return count("select count(*) from follow_up_tasks where escalation_id = ?", escalationId);
  }

  private String statusOf(UUID escalationId) {
    return jdbc.queryForObject("select status from escalations where id = ?", String.class, escalationId);
  }

  @Test
  @DisplayName("happy path: one event → one task, escalation COMPLETED")
  void processesEvent() throws Exception {
    UUID esc = insertEscalation("C-1042");
    UUID eventId = UUID.randomUUID();
    publish(esc.toString(), envelope(eventId, esc, "acme", "C-1042"));

    await().atMost(WAIT).until(() -> tasksFor(esc) == 1);
    assertThat(statusOf(esc)).isEqualTo("COMPLETED");
    assertThat(stats.deliveries(eventId)).isEqualTo(1);
  }

  @Test
  @DisplayName("same event delivered twice: second copy skipped, still one task")
  void duplicateEventIsSkipped() throws Exception {
    UUID esc = insertEscalation("C-1042");
    UUID eventId = UUID.randomUUID();
    String event = envelope(eventId, esc, "acme", "C-1042");
    publish(esc.toString(), event);
    publish(esc.toString(), event);   // what the relay does after a crash between publish and mark

    await().atMost(WAIT).until(() -> stats.duplicatesSkipped(eventId) == 1);
    assertThat(tasksFor(esc)).isEqualTo(1);
    assertThat(count("select count(*) from processed_events where event_id = ?", eventId)).isEqualTo(1);
  }

  @Test
  @DisplayName("crash AFTER DB commit, BEFORE ack: redelivered, deduplicated, one task")
  void crashAfterCommitBeforeAck() throws Exception {
    UUID esc = insertEscalation("C-1042");
    UUID eventId = UUID.randomUUID();
    failPoints.armOnce(EscalationRequestedListener.FAIL_AFTER_DB_COMMIT_BEFORE_ACK, FailPoints.Action.THROW);

    publish(esc.toString(), envelope(eventId, esc, "acme", "C-1042"));

    // Delivery 1 committed the task but never acked → Kafka delivered it again → skipped.
    await().atMost(WAIT).until(() -> stats.duplicatesSkipped(eventId) == 1);
    assertThat(stats.deliveries(eventId)).isEqualTo(2);
    assertThat(tasksFor(esc)).isEqualTo(1);
    assertThat(statusOf(esc)).isEqualTo("COMPLETED");
  }

  @Test
  @DisplayName("crash BEFORE DB commit: everything rolled back, retry does the work once")
  void crashBeforeCommit() throws Exception {
    UUID esc = insertEscalation("C-1042");
    UUID eventId = UUID.randomUUID();
    failPoints.armOnce(EscalationHandler.FAIL_BEFORE_DB_COMMIT, FailPoints.Action.THROW);

    publish(esc.toString(), envelope(eventId, esc, "acme", "C-1042"));

    await().atMost(WAIT).until(() -> tasksFor(esc) == 1);
    assertThat(stats.deliveries(eventId)).isEqualTo(2);
    // The first attempt left nothing behind, so the retry was NOT a duplicate: it did the work.
    assertThat(stats.duplicatesSkipped(eventId)).isZero();
  }

  @Test
  @DisplayName("poison message goes to the dead-letter topic and does NOT block the next message")
  void poisonMessageGoesToDeadLetterTopic() throws Exception {
    UUID esc = insertEscalation("C-1042");
    String key = esc.toString();                       // same key → same partition → right behind it
    publish(key, "this is not json");
    publish(key, envelope(UUID.randomUUID(), esc, "acme", "C-1042"));

    await().atMost(WAIT).until(() -> tasksFor(esc) == 1);   // the good message got through
    await().atMost(WAIT).until(() -> readAll(TOPIC + ".DLT").stream()
        .anyMatch(r -> "this is not json".equals(r.value())));
  }

  @Test
  @DisplayName("event claiming the wrong tenant: rejected to DLT, nothing written, not marked processed")
  void tenantMismatchIsRejected() throws Exception {
    UUID esc = insertEscalation("C-1042");             // belongs to acme
    UUID eventId = UUID.randomUUID();
    publish(esc.toString(), envelope(eventId, esc, "globex", "C-1042"));

    await().atMost(WAIT).until(() -> readAll(TOPIC + ".DLT").stream()
        .anyMatch(r -> r.value().contains(eventId.toString())));
    assertThat(tasksFor(esc)).isZero();
    assertThat(count("select count(*) from processed_events where event_id = ?", eventId))
        .as("rolled back together with the failed work").isZero();
    assertThat(statusOf(esc)).isEqualTo("REQUESTED");
  }

  @Test
  @DisplayName("second safety net: two DIFFERENT event ids for one escalation still give one task")
  void uniqueConstraintIsTheLastLineOfDefense() throws Exception {
    UUID esc = insertEscalation("C-1042");
    UUID first = UUID.randomUUID();
    UUID second = UUID.randomUUID();                   // a bug upstream: a new id for the same work
    publish(esc.toString(), envelope(first, esc, "acme", "C-1042"));
    publish(esc.toString(), envelope(second, esc, "acme", "C-1042"));

    // Dedupe can't catch this (different ids), but UNIQUE(escalation_id) on follow_up_tasks does:
    // the second one fails, is retried, then parked in the DLT.
    await().atMost(WAIT).until(() -> readAll(TOPIC + ".DLT").stream()
        .anyMatch(r -> r.value().contains(second.toString())));
    assertThat(tasksFor(esc)).isEqualTo(1);
  }
}
