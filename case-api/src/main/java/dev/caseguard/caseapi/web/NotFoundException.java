package dev.caseguard.caseapi.web;

/**
 * "Not found OR not yours." Deliberately one exception for both cases, so the response
 * can never reveal whether a record exists in another tenant or another customer's account.
 */
public class NotFoundException extends RuntimeException {

  public NotFoundException() {
    super("not found");
  }
}
