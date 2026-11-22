package dev.caseguard.caseapi.orders;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.LocalDate;
import java.time.OffsetDateTime;

/**
 * A customer's order. Named CustomerOrder (not Order) because ORDER is a reserved word
 * in SQL and JPQL.
 *
 * <p>Read-only in this phase, so there are no setters.
 */
@Entity
@Table(name = "orders")
public class CustomerOrder {

  @Id
  private String id;

  @Column(name = "tenant_id", nullable = false)
  private String tenantId;

  @Column(name = "customer_id", nullable = false)
  private String customerId;

  @Column(nullable = false)
  private String description;

  @Column(name = "amount_cents", nullable = false)
  private long amountCents;

  @Column(nullable = false)
  private String status;

  @Column(name = "promised_date", nullable = false)
  private LocalDate promisedDate;

  @Column(name = "delivered_at")
  private OffsetDateTime deliveredAt;

  /** Free text from the customer. UNTRUSTED: may contain anything, including fake "instructions". */
  @Column(name = "customer_note")
  private String customerNote;

  protected CustomerOrder() {
    // required by JPA
  }

  public String getId() { return id; }
  public String getTenantId() { return tenantId; }
  public String getCustomerId() { return customerId; }
  public String getDescription() { return description; }
  public long getAmountCents() { return amountCents; }
  public String getStatus() { return status; }
  public LocalDate getPromisedDate() { return promisedDate; }
  public OffsetDateTime getDeliveredAt() { return deliveredAt; }
  public String getCustomerNote() { return customerNote; }
}
