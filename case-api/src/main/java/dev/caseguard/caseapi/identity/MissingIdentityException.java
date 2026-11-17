package dev.caseguard.caseapi.identity;

/** Thrown when a request arrives without both identity headers. Mapped to HTTP 400. */
public class MissingIdentityException extends RuntimeException {

  public MissingIdentityException() {
    super("Both " + CallerIdentity.TENANT_HEADER + " and " + CallerIdentity.CUSTOMER_HEADER
        + " headers are required");
  }
}
