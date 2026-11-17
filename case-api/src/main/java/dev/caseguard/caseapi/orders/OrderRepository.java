package dev.caseguard.caseapi.orders;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface OrderRepository extends JpaRepository<CustomerOrder, String> {

  /**
   * The ONLY way the API reads an order: by id AND owner.
   * Spring Data generates:
   *   select ... from orders where id = ? and tenant_id = ? and customer_id = ?
   * Someone else's order simply isn't found.
   */
  Optional<CustomerOrder> findByIdAndTenantIdAndCustomerId(String id, String tenantId, String customerId);
}
