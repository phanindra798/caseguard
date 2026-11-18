package dev.caseguard.worker;

/**
 * A message that can never succeed (bad JSON, missing fields, unknown escalation, tenant mismatch).
 * Retrying would loop forever, so the error handler sends it straight to the dead-letter topic.
 */
public class InvalidEventException extends RuntimeException {

  public InvalidEventException(String message) {
    super(message);
  }
}
