package dev.caseguard.caseapi.web;

import dev.caseguard.caseapi.identity.CallerIdentityResolver;
import java.util.List;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.method.support.HandlerMethodArgumentResolver;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/** Registers the CallerIdentity resolver with Spring MVC. */
@Configuration
public class WebConfig implements WebMvcConfigurer {

  private final CallerIdentityResolver callerIdentityResolver;

  public WebConfig(CallerIdentityResolver callerIdentityResolver) {
    this.callerIdentityResolver = callerIdentityResolver;
  }

  @Override
  public void addArgumentResolvers(List<HandlerMethodArgumentResolver> resolvers) {
    resolvers.add(callerIdentityResolver);
  }
}
