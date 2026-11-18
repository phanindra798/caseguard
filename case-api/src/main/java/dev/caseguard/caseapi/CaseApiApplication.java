package dev.caseguard.caseapi;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling   // runs the outbox relay on a timer
public class CaseApiApplication {

  public static void main(String[] args) {
    SpringApplication.run(CaseApiApplication.class, args);
  }
}
