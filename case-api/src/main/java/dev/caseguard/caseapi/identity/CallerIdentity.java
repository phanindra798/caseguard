package dev.caseguard.caseapi.identity;

/**
 * Who is making this request: which tenant (company) and which customer.
 *
 * <p>Trust model: the Case API is internal. Only the agent service calls it, and the agent
 * takes these values from the customer's authenticated session, never from chat text or the LLM.
 * In production an API gateway would set these headers after checking a login token.
 *
 * <p>Every data lookup in this service must be filtered by BOTH values.
 */
public record CallerIdentity(String tenantId, String customerId) {

  public static final String TENANT_HEADER = "X-Tenant-Id";
  public static final String CUSTOMER_HEADER = "X-Customer-Id";
}
