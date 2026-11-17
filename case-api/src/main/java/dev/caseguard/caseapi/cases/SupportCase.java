package dev.caseguard.caseapi.cases;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import jakarta.persistence.Version;
import java.time.OffsetDateTime;

/**
 * A support case about an order. Named SupportCase because "case" is a Java keyword.
 */
@Entity
@Table(name = "cases")
public class SupportCase {

  @Id
  private String id;

  @Column(name = "tenant_id", nullable = false)
  private String tenantId;

  @Column(name = "customer_id", nullable = false)
  private String customerId;

  @Column(name = "order_id", nullable = false)
  private String orderId;

  @Column(nullable = false)
  private String subject;

  @Column(nullable = false)
  private String status;

  /**
   * Optimistic locking: Hibernate adds "where version = ?" to every update and bumps it.
   * If two writers race, the second update matches 0 rows and fails instead of silently
   * overwriting. Release 2 also uses this number to bind manager approvals to the exact
   * case state they approved.
   */
  @Version
  @Column(nullable = false)
  private int version;

  @Column(name = "updated_at", nullable = false)
  private OffsetDateTime updatedAt;

  protected SupportCase() {
    // required by JPA
  }

  public String getId() { return id; }
  public String getTenantId() { return tenantId; }
  public String getCustomerId() { return customerId; }
  public String getOrderId() { return orderId; }
  public String getSubject() { return subject; }
  public String getStatus() { return status; }
  public int getVersion() { return version; }
  public OffsetDateTime getUpdatedAt() { return updatedAt; }
}
