package dev.caseguard.caseapi.cases;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface CaseRepository extends JpaRepository<SupportCase, String> {

  Optional<SupportCase> findByIdAndTenantIdAndCustomerId(String id, String tenantId, String customerId);

  List<SupportCase> findByOrderIdAndTenantIdAndCustomerIdOrderByIdAsc(
      String orderId, String tenantId, String customerId);
}
