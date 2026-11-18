package dev.caseguard.caseapi.escalations;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * A request to escalate a support case. Created as REQUESTED by the API; the worker
 * (Phase 1.3) moves it to COMPLETED after creating the follow-up task.
 */
@Entity
@Table(name = "escalations")
public class Escalation {

  public enum Status { REQUESTED, COMPLETED, FAILED }

  @Id
  @GeneratedValue(strategy = GenerationType.UUID)   // id assigned by Hibernate on insert
  private UUID id;

  @Column(name = "tenant_id", nullable = false)
  private String tenantId;

  @Column(name = "customer_id", nullable = false)
  private String customerId;

  @Column(name = "case_id", nullable = false)
  private String caseId;

  @Column(name = "operation_id", nullable = false, unique = true)
  private UUID operationId;

  @Column(nullable = false)
  private String status;

  @Column(nullable = false)
  private String reason;

  @Column(name = "policy_ref", nullable = false)
  private String policyRef;

  @Column(name = "requested_at", nullable = false)
  private OffsetDateTime requestedAt;

  @Column(name = "completed_at")
  private OffsetDateTime completedAt;

  protected Escalation() {
    // required by JPA
  }

  /** Factory: the only way to create a new escalation, always in REQUESTED state. */
  public static Escalation requested(String tenantId, String customerId, String caseId,
                                     UUID operationId, String reason, String policyRef) {
    Escalation e = new Escalation();
    e.tenantId = tenantId;
    e.customerId = customerId;
    e.caseId = caseId;
    e.operationId = operationId;
    e.status = Status.REQUESTED.name();
    e.reason = reason;
    e.policyRef = policyRef;
    e.requestedAt = OffsetDateTime.now();
    return e;
  }

  /** Is this escalation the same request as (caller, case)? Used for idempotent replays. */
  boolean belongsTo(String tenantId, String customerId, String caseId) {
    return this.tenantId.equals(tenantId)
        && this.customerId.equals(customerId)
        && this.caseId.equals(caseId);
  }

  public UUID getId() { return id; }
  public String getTenantId() { return tenantId; }
  public String getCustomerId() { return customerId; }
  public String getCaseId() { return caseId; }
  public UUID getOperationId() { return operationId; }
  public String getStatus() { return status; }
  public String getReason() { return reason; }
  public String getPolicyRef() { return policyRef; }
  public OffsetDateTime getRequestedAt() { return requestedAt; }
  public OffsetDateTime getCompletedAt() { return completedAt; }
}
