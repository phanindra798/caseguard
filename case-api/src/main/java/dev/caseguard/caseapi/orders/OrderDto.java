package dev.caseguard.caseapi.orders;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * What the API returns for an order. A separate DTO (not the entity) means:
 *  - we choose exactly which fields leave the service (no tenantId, no internal columns);
 *  - the database schema can change without breaking the API contract the agent depends on.
 */
public record OrderDto(
    String id,
    String customerId,
    String description,
    long amountCents,
    String status,
    LocalDate promisedDate,
    OffsetDateTime deliveredAt) {

  static OrderDto from(CustomerOrder o) {
    return new OrderDto(o.getId(), o.getCustomerId(), o.getDescription(), o.getAmountCents(),
        o.getStatus(), o.getPromisedDate(), o.getDeliveredAt());
  }
}
