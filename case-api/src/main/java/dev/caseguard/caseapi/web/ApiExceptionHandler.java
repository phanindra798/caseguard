package dev.caseguard.caseapi.web;

import dev.caseguard.caseapi.escalations.AlreadyOpenException;
import dev.caseguard.caseapi.escalations.CaseNotOpenException;
import dev.caseguard.caseapi.escalations.OperationConflictException;
import dev.caseguard.caseapi.identity.MissingIdentityException;
import java.util.Map;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

/**
 * Turns exceptions into consistent JSON errors (ProblemDetail, RFC 9457).
 * Every error carries a stable machine-readable {@code code} that the agent's tool layer
 * branches on. The human-readable {@code detail} may change; the {@code code} must not.
 *
 * <pre>
 *  400 missing_identity | missing_idempotency_key | invalid_request
 *  404 not_found
 *  409 already_open | case_not_open | operation_conflict
 * </pre>
 */
@RestControllerAdvice
public class ApiExceptionHandler {

  @ExceptionHandler(NotFoundException.class)
  ProblemDetail notFound(NotFoundException e) {
    // Same body whether the record is missing or belongs to someone else. No hints.
    return problem(HttpStatus.NOT_FOUND, "not_found", "Resource not found");
  }

  @ExceptionHandler(MissingIdentityException.class)
  ProblemDetail missingIdentity(MissingIdentityException e) {
    return problem(HttpStatus.BAD_REQUEST, "missing_identity", e.getMessage());
  }

  @ExceptionHandler(MissingRequestHeaderException.class)
  ProblemDetail missingHeader(MissingRequestHeaderException e) {
    if ("Idempotency-Key".equalsIgnoreCase(e.getHeaderName())) {
      return problem(HttpStatus.BAD_REQUEST, "missing_idempotency_key",
          "Idempotency-Key header (a UUID) is required");
    }
    return problem(HttpStatus.BAD_REQUEST, "invalid_request", e.getMessage());
  }

  @ExceptionHandler(MethodArgumentTypeMismatchException.class)
  ProblemDetail typeMismatch(MethodArgumentTypeMismatchException e) {
    if ("operationId".equals(e.getName())) {
      return problem(HttpStatus.BAD_REQUEST, "missing_idempotency_key", "operation id must be a UUID");
    }
    return problem(HttpStatus.BAD_REQUEST, "invalid_request", "invalid value for " + e.getName());
  }

  @ExceptionHandler(MethodArgumentNotValidException.class)
  ProblemDetail invalidBody(MethodArgumentNotValidException e) {
    ProblemDetail pd = problem(HttpStatus.BAD_REQUEST, "invalid_request", "Request body is invalid");
    Map<String, String> fields = e.getBindingResult().getFieldErrors().stream()
        .collect(Collectors.toMap(f -> f.getField(), f -> String.valueOf(f.getDefaultMessage()), (a, b) -> a));
    pd.setProperty("fields", fields);
    return pd;
  }

  @ExceptionHandler({HttpMessageNotReadableException.class, MissingServletRequestParameterException.class})
  ProblemDetail unreadable(Exception e) {
    return problem(HttpStatus.BAD_REQUEST, "invalid_request", "Malformed request");
  }

  @ExceptionHandler(AlreadyOpenException.class)
  ProblemDetail alreadyOpen(AlreadyOpenException e) {
    ProblemDetail pd = problem(HttpStatus.CONFLICT, "already_open", "Case already has an open escalation");
    pd.setProperty("existingEscalationId", e.getExistingEscalationId());
    return pd;
  }

  @ExceptionHandler(CaseNotOpenException.class)
  ProblemDetail caseNotOpen(CaseNotOpenException e) {
    return problem(HttpStatus.CONFLICT, "case_not_open", e.getMessage());
  }

  @ExceptionHandler(OperationConflictException.class)
  ProblemDetail operationConflict(OperationConflictException e) {
    return problem(HttpStatus.CONFLICT, "operation_conflict",
        "This Idempotency-Key was already used for a different request");
  }

  private static ProblemDetail problem(HttpStatus status, String code, String detail) {
    ProblemDetail pd = ProblemDetail.forStatusAndDetail(status, detail);
    pd.setProperty("code", code);
    return pd;
  }
}
