package dev.caseguard.caseapi.escalations;

import java.time.OffsetDateTime;
import java.util.UUID;

/** What the API returns for an escalation. */
public record EscalationDto(
    UUID id,
    String caseId,
    UUID operationId,
    String status,
    String reason,
    String policyRef,
    OffsetDateTime requestedAt,
    OffsetDateTime completedAt) {

  static EscalationDto from(Escalation e) {
    return new EscalationDto(e.getId(), e.getCaseId(), e.getOperationId(), e.getStatus(),
        e.getReason(), e.getPolicyRef(), e.getRequestedAt(), e.getCompletedAt());
  }
}
