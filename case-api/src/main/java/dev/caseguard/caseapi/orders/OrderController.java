package dev.caseguard.caseapi.orders;

import dev.caseguard.caseapi.identity.CallerIdentity;
import dev.caseguard.caseapi.web.NotFoundException;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class OrderController {

  private final OrderRepository orders;

  public OrderController(OrderRepository orders) {
    this.orders = orders;
  }

  /** GET /orders: the caller's own orders (the agent uses this to ask "which order do you mean?"). */
  @GetMapping("/orders")
  public List<OrderDto> listOrders(CallerIdentity caller) {
    return orders.findByTenantIdAndCustomerIdOrderByPromisedDateDesc(caller.tenantId(), caller.customerId())
        .stream().map(OrderDto::from).toList();
  }

  /** GET /orders/{orderId}: 200 if it's the caller's order, otherwise 404. */
  @GetMapping("/orders/{orderId}")
  public OrderDto getOrder(@PathVariable String orderId, CallerIdentity caller) {
    return orders.findByIdAndTenantIdAndCustomerId(orderId, caller.tenantId(), caller.customerId())
        .map(OrderDto::from)
        .orElseThrow(NotFoundException::new);
  }
}
