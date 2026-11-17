package dev.caseguard.caseapi;

import org.springframework.boot.flyway.autoconfigure.FlywayMigrationStrategy;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.context.annotation.Bean;

/**
 * Test-only: drop everything and re-run all migrations + seed data when the test context
 * starts, so every test run begins from the same known database state.
 */
@TestConfiguration
public class FreshDatabaseConfig {

  @Bean
  FlywayMigrationStrategy cleanThenMigrate() {
    return flyway -> {
      flyway.clean();
      flyway.migrate();
    };
  }
}
