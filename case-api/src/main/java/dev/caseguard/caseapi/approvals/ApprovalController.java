package dev.caseguard.caseapi.approvals;

import dev.caseguard.caseapi.approvals.ApprovalDtos.ApprovalDto;
import dev.caseguard.caseapi.approvals.ApprovalDtos.ApprovalRequest;
import dev.caseguard.caseapi.approvals.ApprovalDtos.DecisionRequest;
import dev.caseguard.caseapi.identity.CallerIdentity;
import dev.caseguard.caseapi.identity.SupervisorIdentity;
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
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ApprovalController {

  private final ApprovalService service;

  public ApprovalController(ApprovalService service) {
    this.service = service;
  }

  // ------------------------------------------------------------------ customer side (the agent)

  @Operation(summary = "Ask a supervisor to approve an escalation (idempotent by Idempotency-Key = operation id)")
  @ApiResponse(responseCode = "201", description = "Approval requested, status PENDING")
  @ApiResponse(responseCode = "200", description = "Replay: this operation already asked")
  @ApiResponse(responseCode = "404", description = "not_found",
      content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
  @ApiResponse(responseCode = "409", description = "case_changed | operation_conflict",
      content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
  @PostMapping("/cases/{caseId}/approvals")
  public ResponseEntity<ApprovalDto> request(@PathVariable String caseId,
                                             @RequestHeader("Idempotency-Key") UUID operationId,
                                             @Valid @RequestBody ApprovalRequest body,
                                             CallerIdentity caller) {
    ApprovalService.Result r;
    try {
      r = service.request(caller, caseId, operationId, body);
    } catch (DataIntegrityViolationException raceLost) {
      r = service.request(caller, caseId, operationId, body);   // the winner's row now exists → replay
    }
    ApprovalDto dto = ApprovalDto.from(r.approval());
    return r.created()
        ? ResponseEntity.status(HttpStatus.CREATED).location(URI.create("/approvals/" + dto.id())).body(dto)
        : ResponseEntity.ok().header("Idempotent-Replayed", "true").body(dto);
  }

  @Operation(summary = "Get one of the caller's approvals")
  @GetMapping("/approvals/{approvalId}")
  public ApprovalDto get(@PathVariable UUID approvalId, CallerIdentity caller) {
    return ApprovalDto.from(service.get(caller, approvalId));
  }

  // ------------------------------------------------------------------ supervisor side

  @Operation(summary = "Pending approvals of the supervisor's tenant, oldest first")
  @GetMapping("/supervisor/approvals")
  public List<ApprovalDto> pending(SupervisorIdentity supervisor) {
    return service.pending(supervisor).stream().map(ApprovalDto::from).toList();
  }

  @Operation(summary = "Approve or reject (once only)")
  @ApiResponse(responseCode = "200", description = "Decision recorded")
  @ApiResponse(responseCode = "404", description = "not_found (or another tenant's approval)",
      content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
  @ApiResponse(responseCode = "409", description = "already_decided | approval_expired",
      content = @Content(schema = @Schema(implementation = ProblemDetail.class)))
  @PostMapping("/supervisor/approvals/{approvalId}/decision")
  public ApprovalDto decide(@PathVariable UUID approvalId, @Valid @RequestBody DecisionRequest body,
                            SupervisorIdentity supervisor) {
    return ApprovalDto.from(service.decide(supervisor, approvalId, Approval.Status.valueOf(body.decision())));
  }
}
