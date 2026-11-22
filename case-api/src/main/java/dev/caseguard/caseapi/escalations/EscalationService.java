package dev.caseguard.caseapi.escalations;

import dev.caseguard.caseapi.approvals.ApprovalService;
import dev.caseguard.caseapi.cases.CaseRepository;
import dev.caseguard.caseapi.cases.SupportCase;
import dev.caseguard.caseapi.identity.CallerIdentity;
import dev.caseguard.caseapi.orders.OrderRepository;
import dev.caseguard.caseapi.outbox.OutboxEvent;
import dev.caseguard.caseapi.outbox.OutboxRepository;
import dev.caseguard.caseapi.reliability.FailPoints;
import dev.caseguard.caseapi.tracing.TraceContext;
import dev.caseguard.caseapi.web.NotFoundException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.json.JsonMapper;

/**
 * Escalation rules. The order of checks matters:
 *
 * <pre>
 *  1. ownership     - is this the caller's case?          no  -> 404
 *  2. replay        - has THIS operation id been seen?    yes -> same request? 200 : 409 conflict
 *  3. case state    - is the case OPEN?                   no  -> 409 case_not_open
 *  4. one open      - is an escalation open?              ours -> 200 replay, other -> 409 already_open
 *  5. approval      - order over the limit?               needs a VALID approval, else 403 approval_required
 *  6. write         - escalation row + outbox row, ONE transaction -> 202
 * </pre>
 */
@Service
public class EscalationService {

  public static final String EVENT_TYPE = "EscalationRequested";
  public static final String FAIL_AFTER_ESCALATION_INSERT = "API_AFTER_ESCALATION_INSERT";

  private final CaseRepository cases;
  private final OrderRepository orders;
  private final ApprovalService approvals;
  private final long approvalOverCents;
  private final EscalationRepository escalations;
  private final OutboxRepository outbox;
  private final FailPoints failPoints;
  private final JsonMapper json;

  public EscalationService(CaseRepository cases, OrderRepository orders, ApprovalService approvals,
                           @Value("${caseguard.approval.over-cents:50000}") long approvalOverCents,
                           EscalationRepository escalations, OutboxRepository outbox,
                           FailPoints failPoints, JsonMapper json) {
    this.cases = cases;
    this.orders = orders;
    this.approvals = approvals;
    this.approvalOverCents = approvalOverCents;
    this.escalations = escalations;
    this.outbox = outbox;
    this.failPoints = failPoints;
    this.json = json;
  }

  @Transactional
  public EscalationResult request(CallerIdentity caller, String caseId, UUID operationId,
                                  EscalationRequest req) {
    // 1. Ownership FIRST. Nothing else is looked up for a case that isn't the caller's.
    SupportCase kase = cases.findByIdAndTenantIdAndCustomerId(caseId, caller.tenantId(), caller.customerId())
        .orElseThrow(NotFoundException::new);

    // 2. Idempotent replay: this operation id was already used.
    Optional<Escalation> sameOperation = escalations.findByOperationId(operationId);
    if (sameOperation.isPresent()) {
      return replay(sameOperation.get(), caller, caseId);
    }

    // 3. Only open cases can be escalated.
    if (!"OPEN".equals(kase.getStatus())) {
      throw new CaseNotOpenException(kase.getStatus());
    }

    // 4. Business rule: one open escalation per case (also enforced by a partial unique index).
    Optional<Escalation> open = escalations.findOpenByCaseId(caseId);
    if (open.isPresent()) {
      // Race found by the concurrency test: an identical request (same key) may have committed
      // between step 2 and here. If the open escalation is OURS, this is a replay, not a conflict.
      if (open.get().getOperationId().equals(operationId)) {
        return replay(open.get(), caller, caseId);
      }
      throw new AlreadyOpenException(open.get().getId());
    }

    // 5. Expensive orders need a supervisor's approval for THIS operation, case version and policy.
    //    Enforced HERE, in the backend, so no client (or confused agent) can skip it.
    long amount = orders.findByIdAndTenantIdAndCustomerId(kase.getOrderId(), caller.tenantId(), caller.customerId())
        .orElseThrow(NotFoundException::new).getAmountCents();
    if (amount > approvalOverCents) {
      approvals.requireValidApproval(caller, kase, operationId, req.policyRef(), req.approvalId());
    }

    // 6. Write the business row AND the outbox row in the SAME transaction.
    Escalation e = Escalation.requested(caller.tenantId(), caller.customerId(), caseId,
        operationId, req.reason(), req.policyRef());
    escalations.saveAndFlush(e);   // flush now: constraint violations surface here, not at commit

    failPoints.hit(FAIL_AFTER_ESCALATION_INSERT);   // tests prove a crash here leaves NOTHING behind

    outbox.saveAndFlush(OutboxEvent.of(e.getTenantId(), e.getId(), EVENT_TYPE, payloadFor(e),
        TraceContext.currentTraceParent()));
    return EscalationResult.created(e);
  }

  /**
   * Called when the INSERT lost a race against a concurrent request (unique constraint violation).
   * The first transaction was rolled back, so this runs in a NEW transaction and re-reads
   * what the winner wrote, applying exactly the same rules as above.
   */
  @Transactional(readOnly = true)
  public EscalationResult resolveAfterRace(CallerIdentity caller, String caseId, UUID operationId) {
    cases.findByIdAndTenantIdAndCustomerId(caseId, caller.tenantId(), caller.customerId())
        .orElseThrow(NotFoundException::new);
    Optional<Escalation> sameOperation = escalations.findByOperationId(operationId);
    if (sameOperation.isPresent()) {
      return replay(sameOperation.get(), caller, caseId);
    }
    Escalation open = escalations.findOpenByCaseId(caseId)
        .orElseThrow(() -> new IllegalStateException("constraint violated but no conflicting row found"));
    throw new AlreadyOpenException(open.getId());
  }

  @Transactional(readOnly = true)
  public Escalation get(CallerIdentity caller, UUID escalationId) {
    return escalations.findByIdAndTenantIdAndCustomerId(escalationId, caller.tenantId(), caller.customerId())
        .orElseThrow(NotFoundException::new);
  }

  /** For the agent's "reconcile" step: did my operation create an escalation? Empty = no (or not yours). */
  @Transactional(readOnly = true)
  public List<Escalation> findByOperation(CallerIdentity caller, UUID operationId) {
    return escalations.findByOperationIdAndTenantIdAndCustomerId(operationId, caller.tenantId(), caller.customerId());
  }

  private EscalationResult replay(Escalation existing, CallerIdentity caller, String caseId) {
    // Same key must mean same request: same caller AND same case.
    if (!existing.belongsTo(caller.tenantId(), caller.customerId(), caseId)) {
      throw new OperationConflictException();
    }
    return EscalationResult.replayed(existing);
  }

  /** Event body. The relay wraps it in an envelope carrying the event id (= outbox row id). */
  private String payloadFor(Escalation e) {
    Map<String, Object> p = new LinkedHashMap<>();
    p.put("escalationId", e.getId().toString());
    p.put("operationId", e.getOperationId().toString());
    p.put("tenantId", e.getTenantId());
    p.put("customerId", e.getCustomerId());
    p.put("caseId", e.getCaseId());
    p.put("policyRef", e.getPolicyRef());
    p.put("requestedAt", e.getRequestedAt().toString());
    return json.writeValueAsString(p);
  }
}
