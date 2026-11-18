package dev.caseguard.caseapi.escalations;

import dev.caseguard.caseapi.identity.CallerIdentity;
import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.media.Content;
import io.swagger.v3.oas.annotations.media.Schema;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import jakarta.validation.Valid;
import java.net.URI;
import java.util.List;
import java.util.UUID;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class EscalationController {

  public static final String IDEMPOTENCY_HEADER = "Idempotency-Key";
  public static final String REPLAYED_HEADER = "Idempotent-Replayed";

  private final EscalationService service;

  public EscalationController(EscalationService service) {
    this.service = service;
  }

  /**
   * Request an escalation.
   *
   * <ul>
   *   <li>202 Accepted: new escalation created; the worker will complete it asynchronously.</li>
   *   <li>200 OK + {@code Idempotent-Replayed: true}: this operation was already done; same escalation returned.</li>
   *   <li>409 already_open / case_not_open / operation_conflict, 404 not yours, 400 bad input.</li>
   * </ul>
   */
  @Operation(summary = "Request an escalation (idempotent by Idempotency-Key)")
  @ApiResponse(responseCode = "202", description = "New escalation accepted; worker completes it asynchronously")
  @ApiResponse(responseCode = "200", description = "Replay: this operation already created this escalation")
  @ApiResponse(responseCode = "400", description = "missing_identity | missing_idempotency_key | invalid_request",
      content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
  @ApiResponse(responseCode = "404", description = "not_found (missing or not the caller's case)",
      content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
  @ApiResponse(responseCode = "409", description = "already_open (has existingEscalationId) | case_not_open | operation_conflict",
      content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
  @PostMapping("/cases/{caseId}/escalations")
  public ResponseEntity<EscalationDto> request(
      @PathVariable String caseId,
      @RequestHeader(IDEMPOTENCY_HEADER) UUID operationId,
      @Valid @RequestBody EscalationRequest body,
      CallerIdentity caller) {

    EscalationResult result;
    try {
      result = service.request(caller, caseId, operationId, body);
    } catch (DataIntegrityViolationException raceLost) {
      // A concurrent identical (or competing) request inserted first. Its transaction won;
      // ours rolled back. Re-read in a fresh transaction and answer consistently.
      result = service.resolveAfterRace(caller, caseId, operationId);
    }

    EscalationDto dto = EscalationDto.from(result.escalation());
    if (result.created()) {
      return ResponseEntity.status(HttpStatus.ACCEPTED)
          .location(URI.create("/escalations/" + dto.id()))
          .body(dto);
    }
    return ResponseEntity.ok().header(REPLAYED_HEADER, "true").body(dto);
  }

  /** Poll the status of one escalation (the agent's verify step). */
  @Operation(summary = "Get one escalation (verify step)")
  @ApiResponse(responseCode = "200", description = "The caller's escalation")
  @ApiResponse(responseCode = "404", description = "not_found",
      content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
  @GetMapping("/escalations/{escalationId}")
  public EscalationDto get(@PathVariable UUID escalationId, CallerIdentity caller) {
    return EscalationDto.from(service.get(caller, escalationId));
  }

  /** Find the escalation an operation created, if any (the agent's reconcile step). */
  @Operation(summary = "Find the escalation an operation created (reconcile step); empty list = not created")
  @GetMapping("/escalations")
  public List<EscalationDto> findByOperation(@RequestParam UUID operationId, CallerIdentity caller) {
    return service.findByOperation(caller, operationId).stream().map(EscalationDto::from).toList();
  }
}
