package dev.caseguard.caseapi.approvals;

import dev.caseguard.caseapi.approvals.ApprovalDtos.ApprovalRequest;
import dev.caseguard.caseapi.approvals.ApprovalExceptions.AlreadyDecidedException;
import dev.caseguard.caseapi.approvals.ApprovalExceptions.ApprovalExpiredException;
import dev.caseguard.caseapi.approvals.ApprovalExceptions.ApprovalRequiredException;
import dev.caseguard.caseapi.approvals.ApprovalExceptions.CaseChangedException;
import dev.caseguard.caseapi.cases.CaseRepository;
import dev.caseguard.caseapi.cases.SupportCase;
import dev.caseguard.caseapi.escalations.OperationConflictException;
import dev.caseguard.caseapi.identity.CallerIdentity;
import dev.caseguard.caseapi.identity.SupervisorIdentity;
import dev.caseguard.caseapi.web.NotFoundException;
import java.time.Duration;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Supervisor approvals.
 *
 * <pre>
 *  agent      POST /cases/{id}/approvals  (Idempotency-Key = the escalation's operation id)
 *  supervisor POST /supervisor/approvals/{id}/decision   APPROVED | REJECTED
 *  agent      POST /cases/{id}/escalations  with approvalId → {@link #requireValidApproval} checks the binding
 * </pre>
 */
@Service
public class ApprovalService {

  private final ApprovalRepository approvals;
  private final CaseRepository cases;
  private final Duration ttl;

  public ApprovalService(ApprovalRepository approvals, CaseRepository cases,
                         @Value("${caseguard.approval.ttl-hours:24}") long ttlHours) {
    this.approvals = approvals;
    this.cases = cases;
    this.ttl = Duration.ofHours(ttlHours);
  }

  public record Result(Approval approval, boolean created) {}

  @Transactional
  public Result request(CallerIdentity caller, String caseId, UUID operationId, ApprovalRequest req) {
    // 1. Ownership first.
    SupportCase kase = cases.findByIdAndTenantIdAndCustomerId(caseId, caller.tenantId(), caller.customerId())
        .orElseThrow(NotFoundException::new);
    // 2. Idempotent replay: the same operation already asked for approval.
    Optional<Approval> same = approvals.findByOperationId(operationId);
    if (same.isPresent()) {
      if (!same.get().belongsTo(caller.tenantId(), caller.customerId(), caseId)) {
        throw new OperationConflictException();
      }
      return new Result(same.get(), false);
    }
    // 3. The supervisor must see the case the agent decided on, not a changed one.
    if (kase.getVersion() != req.caseVersion()) {
      throw new CaseChangedException(req.caseVersion(), kase.getVersion());
    }
    Approval a = Approval.pending(caller.tenantId(), caller.customerId(), caseId, operationId,
        kase.getVersion(), req.policyRef(), req.reason(), req.conversationRef(), OffsetDateTime.now().plus(ttl));
    approvals.saveAndFlush(a);
    return new Result(a, true);
  }

  @Transactional(readOnly = true)
  public Approval get(CallerIdentity caller, UUID id) {
    return approvals.findByIdAndTenantIdAndCustomerId(id, caller.tenantId(), caller.customerId())
        .orElseThrow(NotFoundException::new);
  }

  @Transactional(readOnly = true)
  public List<Approval> pending(SupervisorIdentity supervisor) {
    return approvals.findByTenantIdAndStatusOrderByCreatedAtAsc(supervisor.tenantId(), "PENDING");
  }

  @Transactional
  public Approval decide(SupervisorIdentity supervisor, UUID id, Approval.Status decision) {
    // A supervisor only sees and decides approvals of THEIR tenant.
    Approval a = approvals.findByIdAndTenantId(id, supervisor.tenantId()).orElseThrow(NotFoundException::new);
    if (!"PENDING".equals(a.getStatus())) {
      throw new AlreadyDecidedException(a.getStatus());
    }
    if (a.isExpired()) {
      throw new ApprovalExpiredException();
    }
    a.decide(decision, supervisor.supervisorId());
    return a;
  }

  /**
   * The backend's guard for an escalation that needs approval. Every binding must hold:
   * same caller, same case, same operation, APPROVED, not expired, case unchanged, same policy.
   * Called inside the escalation transaction.
   */
  public void requireValidApproval(CallerIdentity caller, SupportCase kase, UUID operationId,
                                   String policyRef, UUID approvalId) {
    if (approvalId == null) {
      throw new ApprovalRequiredException("missing");
    }
    Approval a = approvals.findByIdAndTenantIdAndCustomerId(approvalId, caller.tenantId(), caller.customerId())
        .orElseThrow(() -> new ApprovalRequiredException("not_found"));
    if (!a.getCaseId().equals(kase.getId())) {
      throw new ApprovalRequiredException("wrong_case");
    }
    if (!a.getOperationId().equals(operationId)) {
      throw new ApprovalRequiredException("wrong_operation");
    }
    if (!"APPROVED".equals(a.getStatus())) {
      throw new ApprovalRequiredException(a.getStatus().toLowerCase());     // "pending" or "rejected"
    }
    if (a.isExpired()) {
      throw new ApprovalRequiredException("expired");
    }
    if (a.getCaseVersion() != kase.getVersion()) {
      throw new ApprovalRequiredException("case_changed");
    }
    if (!a.getPolicyRef().equals(policyRef)) {
      throw new ApprovalRequiredException("policy_changed");
    }
  }
}
