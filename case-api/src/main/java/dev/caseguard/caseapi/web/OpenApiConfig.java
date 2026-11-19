package dev.caseguard.caseapi.web;

import dev.caseguard.caseapi.identity.CallerIdentity;
import dev.caseguard.caseapi.identity.SupervisorIdentity;
import java.util.Arrays;
import io.swagger.v3.oas.models.OpenAPI;
import io.swagger.v3.oas.models.info.Info;
import io.swagger.v3.oas.models.media.StringSchema;
import io.swagger.v3.oas.models.parameters.HeaderParameter;
import org.springdoc.core.customizers.OperationCustomizer;
import org.springdoc.core.utils.SpringDocUtils;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/** Metadata for the generated OpenAPI contract (served at /v3/api-docs, UI at /swagger-ui.html). */
@Configuration
public class OpenApiConfig {

  static {
    // CallerIdentity is filled by our resolver from headers; it is not a real request parameter.
    SpringDocUtils.getConfig().addRequestWrapperToIgnore(CallerIdentity.class, SupervisorIdentity.class);
  }

  @Bean
  OpenAPI caseApiOpenApi() {
    return new OpenAPI().info(new Info()
        .title("CaseGuard Case API")
        .version("0.2.0")
        .description("""
            The only door into CRM data. Every call needs X-Tenant-Id and X-Customer-Id \
            (from the trusted session). Errors are RFC 9457 ProblemDetail with a stable `code`."""));
  }

  /** Document the two identity headers on every endpoint, since the resolver reads them for all. */
  @Bean
  OperationCustomizer identityHeaders() {
    return (operation, handlerMethod) -> {
      boolean supervisor = Arrays.stream(handlerMethod.getMethodParameters())
          .anyMatch(p -> p.getParameterType().equals(SupervisorIdentity.class));
      operation.addParametersItem(new HeaderParameter().name(CallerIdentity.TENANT_HEADER)
          .required(true).schema(new StringSchema()).description("Tenant (company) id from the trusted session"));
      if (supervisor) {
        operation.addParametersItem(new HeaderParameter().name(SupervisorIdentity.SUPERVISOR_HEADER)
            .required(true).schema(new StringSchema()).description("Supervisor id from the trusted session"));
      } else {
        operation.addParametersItem(new HeaderParameter().name(CallerIdentity.CUSTOMER_HEADER)
            .required(true).schema(new StringSchema()).description("Customer id from the trusted session"));
      }
      return operation;
    };
  }
}
