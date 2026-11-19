package dev.caseguard.caseapi.identity;

import org.springframework.core.MethodParameter;
import org.springframework.stereotype.Component;
import org.springframework.web.bind.support.WebDataBinderFactory;
import org.springframework.web.context.request.NativeWebRequest;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.method.support.ModelAndViewContainer;

/**
 * Builds a {@link CallerIdentity} (customer) or {@link SupervisorIdentity} from the request headers, in ONE place.
 *
 * <p>Any controller method that declares a {@code CallerIdentity} parameter gets it filled in
 * automatically. A controller can't "forget" to read the headers, and the validation rules
 * live here instead of being copied into every endpoint.
 */
@Component
public class CallerIdentityResolver implements HandlerMethodArgumentResolver {

  @Override
  public boolean supportsParameter(MethodParameter parameter) {
    return parameter.getParameterType().equals(CallerIdentity.class)
        || parameter.getParameterType().equals(SupervisorIdentity.class);
  }

  @Override
  public Object resolveArgument(MethodParameter parameter, ModelAndViewContainer mav,
                                        NativeWebRequest request, WebDataBinderFactory binders) {
    String tenantId = request.getHeader(CallerIdentity.TENANT_HEADER);
    if (parameter.getParameterType().equals(SupervisorIdentity.class)) {
      String supervisorId = request.getHeader(SupervisorIdentity.SUPERVISOR_HEADER);
      if (isBlank(tenantId) || isBlank(supervisorId)) {
        throw new MissingIdentityException();
      }
      return new SupervisorIdentity(tenantId.trim(), supervisorId.trim());
    }
    String customerId = request.getHeader(CallerIdentity.CUSTOMER_HEADER);
    if (isBlank(tenantId) || isBlank(customerId)) {
      throw new MissingIdentityException();
    }
    return new CallerIdentity(tenantId.trim(), customerId.trim());
  }

  private static boolean isBlank(String s) {
    return s == null || s.isBlank();
  }
}
