package dev.caseguard.caseapi.escalations;

/**
 * The Idempotency-Key was already used for a DIFFERENT request (another case or another caller).
 * That's a client bug, never a normal retry. HTTP 409 with no details about the other request.
 */
public class OperationConflictException extends RuntimeException {

  public OperationConflictException() {
    super("operation id already used for a different request");
  }
}
