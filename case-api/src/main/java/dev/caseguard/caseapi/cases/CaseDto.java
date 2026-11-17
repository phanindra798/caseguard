package dev.caseguard.caseapi.cases;

import java.time.OffsetDateTime;

/** What the API returns for a support case. The version is included because approvals bind to it later. */
public record CaseDto(
    String id,
    String orderId,
    String subject,
    String status,
    int version,
    OffsetDateTime updatedAt) {

  static CaseDto from(SupportCase c) {
    return new CaseDto(c.getId(), c.getOrderId(), c.getSubject(), c.getStatus(),
        c.getVersion(), c.getUpdatedAt());
  }
}
