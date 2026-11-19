package dev.caseguard.caseapi.identity;

/**
 * A support supervisor, scoped to ONE tenant: they can only see and decide that tenant's approvals.
 * Same trust model as {@link CallerIdentity}: in production a gateway sets these headers after login.
 */
public record SupervisorIdentity(String tenantId, String supervisorId) {

  public static final String SUPERVISOR_HEADER = "X-Supervisor-Id";
}
