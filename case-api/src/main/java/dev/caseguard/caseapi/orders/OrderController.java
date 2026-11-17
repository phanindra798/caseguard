package dev.caseguard.caseapi.orders;

import dev.caseguard.caseapi.identity.CallerIdentity;
import dev.caseguard.caseapi.web.NotFoundException;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {

  private final OrderRepository orders;

  public OrderController(OrderRepository orders) {
    this.orders = orders;
  }

  /** GET /orders/{orderId}: 200 if it's the caller's order, otherwise 404. */
  @GetMapping("/orders/{orderId}")
  public OrderDto getOrder(@PathVariable String orderId, CallerIdentity caller) {
    return orders.findByIdAndTenantIdAndCustomerId(orderId, caller.tenantId(), caller.customerId())
        .map(OrderDto::from)
        .orElseThrow(NotFoundException::new);
  }
}
