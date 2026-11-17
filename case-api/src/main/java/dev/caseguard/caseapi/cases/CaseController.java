package dev.caseguard.caseapi.cases;

import dev.caseguard.caseapi.identity.CallerIdentity;
import dev.caseguard.caseapi.web.NotFoundException;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class CaseController {

  private final CaseRepository cases;

  public CaseController(CaseRepository cases) {
    this.cases = cases;
  }

  /** GET /cases/{caseId}: 200 if it's the caller's case, otherwise 404. */
  @GetMapping("/cases/{caseId}")
  public CaseDto getCase(@PathVariable String caseId, CallerIdentity caller) {
    return cases.findByIdAndTenantIdAndCustomerId(caseId, caller.tenantId(), caller.customerId())
        .map(CaseDto::from)
        .orElseThrow(NotFoundException::new);
  }

  /**
   * GET /cases?orderId=1042: the caller's cases for that order.
   * Returns an EMPTY list (not 404) for someone else's order. From the outside,
   * "not yours" and "no cases" look identical.
   */
  @GetMapping("/cases")
  public List<CaseDto> findByOrder(@RequestParam String orderId, CallerIdentity caller) {
    return cases.findByOrderIdAndTenantIdAndCustomerIdOrderByIdAsc(
            orderId, caller.tenantId(), caller.customerId())
        .stream()
        .map(CaseDto::from)
        .toList();
  }
}
