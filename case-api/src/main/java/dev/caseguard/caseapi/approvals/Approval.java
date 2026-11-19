package dev.caseguard.caseapi.approvals;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;

/**
 * A supervisor's permission for ONE exact escalation: this operation, on this case version,
 * under this policy version. Anything else and it doesn't count.
 */
@Entity
@Table(name = "approvals")
public class Approval {

  public enum Status { PENDING, APPROVED, REJECTED }

  @Id
  @GeneratedValue(strategy = GenerationType.UUID)
  private UUID id;

  @Column(name = "tenant_id", nullable = false)
  private String tenantId;

  @Column(name = "customer_id", nullable = false)
  private String customerId;

  @Column(name = "case_id", nullable = false)
  private String caseId;

  @Column(name = "operation_id", nullable = false, unique = true)
  private UUID operationId;

  @Column(name = "case_version", nullable = false)
  private int caseVersion;

  @Column(name = "policy_ref", nullable = false)
  private String policyRef;

  @Column(nullable = false)
  private String reason;

  @Column(name = "conversation_ref")
  private String conversationRef;

  @Column(nullable = false)
  private String status;

  @Column(name = "decided_by")
  private String decidedBy;

  @Column(name = "decided_at")
  private OffsetDateTime decidedAt;

  @Column(name = "created_at", nullable = false)
  private OffsetDateTime createdAt;

  @Column(name = "expires_at", nullable = false)
  private OffsetDateTime expiresAt;

  protected Approval() {
    // required by JPA
  }

  static Approval pending(String tenantId, String customerId, String caseId, UUID operationId,
                          int caseVersion, String policyRef, String reason, String conversationRef,
                          OffsetDateTime expiresAt) {
    Approval a = new Approval();
    a.tenantId = tenantId;
    a.customerId = customerId;
    a.caseId = caseId;
    a.operationId = operationId;
    a.caseVersion = caseVersion;
    a.policyRef = policyRef;
    a.reason = reason;
    a.conversationRef = conversationRef;
    a.status = Status.PENDING.name();
    a.createdAt = OffsetDateTime.now();
    a.expiresAt = expiresAt;
    return a;
  }

  void decide(Status decision, String supervisorId) {
    this.status = decision.name();
    this.decidedBy = supervisorId;
    this.decidedAt = OffsetDateTime.now();
  }

  boolean isExpired() {
    return OffsetDateTime.now().isAfter(expiresAt);
  }

  boolean belongsTo(String tenantId, String customerId, String caseId) {
    return this.tenantId.equals(tenantId) && this.customerId.equals(customerId) && this.caseId.equals(caseId);
  }

  public UUID getId() { return id; }
  public String getTenantId() { return tenantId; }
  public String getCustomerId() { return customerId; }
  public String getCaseId() { return caseId; }
  public UUID getOperationId() { return operationId; }
  public int getCaseVersion() { return caseVersion; }
  public String getPolicyRef() { return policyRef; }
  public String getReason() { return reason; }
  public String getConversationRef() { return conversationRef; }
  public String getStatus() { return status; }
  public String getDecidedBy() { return decidedBy; }
  public OffsetDateTime getDecidedAt() { return decidedAt; }
  public OffsetDateTime getCreatedAt() { return createdAt; }
  public OffsetDateTime getExpiresAt() { return expiresAt; }
}
