package dev.caseguard.caseapi.outbox;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.OffsetDateTime;
import java.util.UUID;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

/**
 * One "message to publish", stored in the same transaction as the business change.
 *
 * <p>The row id doubles as the event id. It never changes, even if the relay publishes the
 * row twice after a crash, so consumers can deduplicate on it.
 */
@Entity
@Table(name = "outbox")
public class OutboxEvent {

  @Id
  @GeneratedValue(strategy = GenerationType.UUID)
  private UUID id;

  @Column(name = "tenant_id", nullable = false)
  private String tenantId;

  @Column(name = "aggregate_id", nullable = false)
  private UUID aggregateId;

  @Column(name = "event_type", nullable = false)
  private String eventType;

  @JdbcTypeCode(SqlTypes.JSON)       // stored as Postgres jsonb
  @Column(nullable = false)
  private String payload;

  @Column(name = "created_at", nullable = false)
  private OffsetDateTime createdAt;

  @Column(name = "published_at")
  private OffsetDateTime publishedAt;

  protected OutboxEvent() {
    // required by JPA
  }

  public static OutboxEvent of(String tenantId, UUID aggregateId, String eventType, String payloadJson) {
    OutboxEvent o = new OutboxEvent();
    o.tenantId = tenantId;
    o.aggregateId = aggregateId;
    o.eventType = eventType;
    o.payload = payloadJson;
    o.createdAt = OffsetDateTime.now();
    return o;
  }

  public UUID getId() { return id; }
  public String getTenantId() { return tenantId; }
  public UUID getAggregateId() { return aggregateId; }
  public String getEventType() { return eventType; }
  public String getPayload() { return payload; }
  public OffsetDateTime getCreatedAt() { return createdAt; }
  public OffsetDateTime getPublishedAt() { return publishedAt; }
}
