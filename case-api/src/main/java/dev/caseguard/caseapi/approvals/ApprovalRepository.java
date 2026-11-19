package dev.caseguard.caseapi.approvals;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.springframework.data.jpa.repository.JpaRepository;

public interface ApprovalRepository extends JpaRepository<Approval, UUID> {

  /** Internal: idempotent replays. Never returned without an ownership check. */
  Optional<Approval> findByOperationId(UUID operationId);

  /** Customer side: only their own approvals. */
  Optional<Approval> findByIdAndTenantIdAndCustomerId(UUID id, String tenantId, String customerId);

  /** Supervisor side: only their own tenant. */
  Optional<Approval> findByIdAndTenantId(UUID id, String tenantId);

  List<Approval> findByTenantIdAndStatusOrderByCreatedAtAsc(String tenantId, String status);
}
