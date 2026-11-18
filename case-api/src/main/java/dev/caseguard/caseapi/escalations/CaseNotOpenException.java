package dev.caseguard.caseapi.escalations;

/** Only OPEN cases can be escalated. HTTP 409. */
public class CaseNotOpenException extends RuntimeException {

  public CaseNotOpenException(String status) {
    super("case is " + status + ", only OPEN cases can be escalated");
  }
}
