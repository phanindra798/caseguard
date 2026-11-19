package dev.caseguard.caseapi.escalations;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.UUID;

/**
 * Body of POST /cases/{caseId}/escalations.
 *
 * @param reason    why the case is being escalated (written by the agent, shown to support staff)
 * @param policyRef which policy version justified it, e.g. "ESC-3@v2"
 * @param approvalId required for orders over the approval limit: an APPROVED approval bound to this
 *                   same operation, case version and policy version
 */
public record EscalationRequest(
    @NotBlank @Size(max = 1000) String reason,
    @NotBlank @Pattern(regexp = "^[A-Z]+-\\d+@v\\d+$", message = "must look like ESC-3@v2") String policyRef,
    UUID approvalId) {
}
