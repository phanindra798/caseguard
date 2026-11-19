package dev.caseguard.caseapi.approvals;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.time.OffsetDateTime;
import java.util.UUID;

/** Request and response shapes for approvals. */
public final class ApprovalDtos {

  private ApprovalDtos() {
  }

  /**
   * Body of POST /cases/{caseId}/approvals.
   *
   * @param caseVersion     the case version the agent based its decision on. If the case has changed
   *                        since, the request is refused (409 case_changed): the agent must look again.
   * @param conversationRef opaque id the agent uses to resume the right conversation later
   */
  public record ApprovalRequest(
      @NotNull Integer caseVersion,
      @NotBlank @Pattern(regexp = "^[A-Z]+-\\d+@v\\d+$", message = "must look like ESC-3@v2") String policyRef,
      @NotBlank @Size(max = 1000) String reason,
      @Size(max = 200) String conversationRef) {
  }

  /** Body of POST /supervisor/approvals/{id}/decision. */
  public record DecisionRequest(
      @NotBlank @Pattern(regexp = "APPROVED|REJECTED", message = "must be APPROVED or REJECTED") String decision) {
  }

  public record ApprovalDto(
      UUID id, String caseId, UUID operationId, int caseVersion, String policyRef, String reason,
      String conversationRef, String status, String decidedBy, OffsetDateTime decidedAt,
      OffsetDateTime createdAt, OffsetDateTime expiresAt) {

    static ApprovalDto from(Approval a) {
      return new ApprovalDto(a.getId(), a.getCaseId(), a.getOperationId(), a.getCaseVersion(), a.getPolicyRef(),
          a.getReason(), a.getConversationRef(), a.getStatus(), a.getDecidedBy(), a.getDecidedAt(),
          a.getCreatedAt(), a.getExpiresAt());
    }
  }
}
