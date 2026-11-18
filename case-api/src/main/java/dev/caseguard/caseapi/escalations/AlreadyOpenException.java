package dev.caseguard.caseapi.escalations;

import java.util.UUID;

/**
 * A DIFFERENT operation tried to escalate a case that already has an open escalation.
 * HTTP 409 with the existing id, so the agent says "already escalated", not "I escalated it".
 */
public class AlreadyOpenException extends RuntimeException {

  private final UUID existingEscalationId;

  public AlreadyOpenException(UUID existingEscalationId) {
    super("case already has an open escalation");
    this.existingEscalationId = existingEscalationId;
  }

  public UUID getExistingEscalationId() {
    return existingEscalationId;
  }
}
