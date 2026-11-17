package dev.caseguard.caseapi.web;

import dev.caseguard.caseapi.identity.MissingIdentityException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

/**
 * Turns exceptions into consistent JSON errors using ProblemDetail (RFC 9457), so the
 * agent's tool layer can rely on one error format.
 */
@RestControllerAdvice
public class ApiExceptionHandler {

  @ExceptionHandler(NotFoundException.class)
  ProblemDetail notFound(NotFoundException e) {
    // Same body whether the record is missing or belongs to someone else. No hints.
    ProblemDetail pd = ProblemDetail.forStatusAndDetail(HttpStatus.NOT_FOUND, "Resource not found");
    pd.setProperty("code", "not_found");
    return pd;
  }

  @ExceptionHandler(MissingIdentityException.class)
  ProblemDetail missingIdentity(MissingIdentityException e) {
    ProblemDetail pd = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, e.getMessage());
    pd.setProperty("code", "missing_identity");
    return pd;
  }
}
