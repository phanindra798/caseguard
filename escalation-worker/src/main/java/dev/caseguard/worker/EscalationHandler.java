package dev.caseguard.worker;

import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

/**
 * Does the actual work for one event, in ONE database transaction:
 *
 * <pre>
 *   BEGIN
 *     INSERT processed_events(event_id) ON CONFLICT DO NOTHING   → 0 rows? duplicate → stop
 *     INSERT follow_up_task  (only if the escalation exists AND belongs to that tenant)
 *     UPDATE escalation SET status = COMPLETED
 *   COMMIT
 * </pre>
 *
 * Because the dedupe row and the work commit together, they can never disagree: either the
 * task exists AND the event is marked processed, or neither.
 */
@Component
public class EscalationHandler {

  public static final String FAIL_BEFORE_DB_COMMIT = "WORKER_BEFORE_DB_COMMIT";

  public enum Outcome { PROCESSED, DUPLICATE }

  private final JdbcTemplate jdbc;
  private final FailPoints failPoints;
  private final String taskQueue;

  public EscalationHandler(JdbcTemplate jdbc, FailPoints failPoints,
                           @Value("${caseguard.worker.task-queue}") String taskQueue) {
    this.jdbc = jdbc;
    this.failPoints = failPoints;
    this.taskQueue = taskQueue;
  }

  @Transactional
  public Outcome process(EscalationRequestedEvent ev) {
    // 1. Dedupe. A redelivered event conflicts on the primary key and inserts 0 rows.
    int fresh = jdbc.update(
        "insert into processed_events (event_id) values (?) on conflict (event_id) do nothing",
        ev.eventId());
    if (fresh == 0) {
      return Outcome.DUPLICATE;
    }

    // 2. Create the task, but don't trust the payload blindly: the escalation must exist
    //    and belong to the tenant the event claims.
    int created = jdbc.update("""
        insert into follow_up_tasks (id, tenant_id, escalation_id, queue)
        select ?, e.tenant_id, e.id, ?
          from escalations e
         where e.id = ? and e.tenant_id = ?
        """, UUID.randomUUID(), taskQueue, ev.escalationId(), ev.tenantId());
    if (created == 0) {
      // Throwing rolls back step 1 too, so the event isn't wrongly marked as processed.
      throw new InvalidEventException("unknown escalation " + ev.escalationId() + " for tenant " + ev.tenantId());
    }

    // 3. Mark the escalation completed (only moves forward from REQUESTED).
    jdbc.update("""
        update escalations set status = 'COMPLETED', completed_at = now()
         where id = ? and status = 'REQUESTED'
        """, ev.escalationId());

    failPoints.hit(FAIL_BEFORE_DB_COMMIT);   // crash here → whole transaction rolls back → safe retry
    return Outcome.PROCESSED;
  }
}
