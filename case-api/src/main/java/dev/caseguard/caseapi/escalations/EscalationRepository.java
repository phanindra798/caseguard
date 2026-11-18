package dev.caseguard.caseapi.escalations;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;

public interface EscalationRepository extends JpaRepository<Escalation, UUID> {

  /** Internal: used to detect idempotent replays. NOT exposed to callers without an ownership check. */
  Optional<Escalation> findByOperationId(UUID operationId);

  /** The open (non-failed) escalation for a case, if any. Mirrors the partial unique index. */
  @Query("select e from Escalation e where e.caseId = :caseId and e.status <> 'FAILED'")
  Optional<Escalation> findOpenByCaseId(String caseId);

  /** Ownership-checked lookups for the public endpoints. */
  Optional<Escalation> findByIdAndTenantIdAndCustomerId(UUID id, String tenantId, String customerId);

  List<Escalation> findByOperationIdAndTenantIdAndCustomerId(UUID operationId, String tenantId, String customerId);
}
