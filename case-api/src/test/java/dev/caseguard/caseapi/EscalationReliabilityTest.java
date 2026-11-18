package dev.caseguard.caseapi;

import static org.assertj.core.api.Assertions.assertThat;

import dev.caseguard.caseapi.escalations.EscalationService;
import dev.caseguard.caseapi.reliability.FailPoints;
import java.net.http.HttpResponse;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.RepeatedTest;
import org.junit.jupiter.api.Test;

/**
 * Phase 1.2 reliability proofs:
 *  - concurrent duplicates create exactly ONE escalation and ONE outbox event;
 *  - a failure halfway through leaves NOTHING behind (atomicity), and a retry then succeeds.
 */
class EscalationReliabilityTest extends ApiTestBase {

  private static final int THREADS = 20;

  /** Fire THREADS requests at exactly the same moment and collect the responses. */
  private List<HttpResponse<String>> fireTogether(Function<Integer, Callable<HttpResponse<String>>> request)
      throws Exception {
    ExecutorService pool = Executors.newFixedThreadPool(THREADS);
    CountDownLatch start = new CountDownLatch(1);
    try {
      List<Future<HttpResponse<String>>> futures = new ArrayList<>();
      for (int i = 0; i < THREADS; i++) {
        Callable<HttpResponse<String>> call = request.apply(i);
        futures.add(pool.submit(() -> {
          start.await();          // every thread waits here...
          return call.call();
        }));
      }
      start.countDown();          // ...then all go at once
      List<HttpResponse<String>> out = new ArrayList<>();
      for (Future<HttpResponse<String>> f : futures) {
        out.add(f.get());
      }
      return out;
    } finally {
      pool.shutdownNow();
    }
  }

  private static Map<Integer, Long> byStatus(List<HttpResponse<String>> responses) {
    return responses.stream().collect(Collectors.groupingBy(HttpResponse::statusCode, Collectors.counting()));
  }

  @RepeatedTest(3)
  @DisplayName("20 simultaneous requests with the SAME key: one 202, the rest 200 replays, one row")
  void concurrentSameKey() throws Exception {
    UUID key = UUID.randomUUID();
    List<HttpResponse<String>> responses =
        fireTogether(i -> () -> escalate("acme", "cust-A1", "C-1042", key));

    Map<Integer, Long> statuses = byStatus(responses);
    assertThat(statuses).containsOnlyKeys(202, 200);
    assertThat(statuses.get(202)).isEqualTo(1);
    // Every response points at the SAME escalation.
    assertThat(responses.stream().map(r -> body(r).get("id").asString()).distinct()).hasSize(1);
    assertThat(count("select count(*) from escalations")).isEqualTo(1);
    assertThat(count("select count(*) from outbox")).isEqualTo(1);
  }

  @RepeatedTest(3)
  @DisplayName("20 simultaneous requests with DIFFERENT keys on one case: one 202, the rest 409 already_open")
  void concurrentDifferentKeysSameCase() throws Exception {
    List<HttpResponse<String>> responses =
        fireTogether(i -> () -> escalate("acme", "cust-A1", "C-1042", UUID.randomUUID()));

    Map<Integer, Long> statuses = byStatus(responses);
    assertThat(statuses).containsOnlyKeys(202, 409);
    assertThat(statuses.get(202)).isEqualTo(1);
    assertThat(responses.stream().filter(r -> r.statusCode() == 409)
        .map(r -> body(r).get("code").asString())).containsOnly("already_open");
    assertThat(count("select count(*) from escalations")).isEqualTo(1);
    assertThat(count("select count(*) from outbox")).isEqualTo(1);
  }

  @Test
  @DisplayName("failure after the escalation insert: whole transaction rolls back, retry then succeeds")
  void failureMidTransactionLeavesNothingBehind() throws Exception {
    UUID key = UUID.randomUUID();
    failPoints.arm(EscalationService.FAIL_AFTER_ESCALATION_INSERT, FailPoints.Action.THROW);

    HttpResponse<String> crashed = escalate("acme", "cust-A1", "C-1042", key);
    assertThat(crashed.statusCode()).isEqualTo(500);
    // The escalation row was inserted (and flushed) BEFORE the failure, yet nothing survived:
    // no escalation without its event, no event without its escalation.
    assertThat(count("select count(*) from escalations")).isZero();
    assertThat(count("select count(*) from outbox")).isZero();

    // The agent retries with the SAME key after the failure: it works normally.
    failPoints.disarm();
    HttpResponse<String> retry = escalate("acme", "cust-A1", "C-1042", key);
    assertThat(retry.statusCode()).isEqualTo(202);
    assertThat(count("select count(*) from escalations")).isEqualTo(1);
    assertThat(count("select count(*) from outbox")).isEqualTo(1);
  }
}
