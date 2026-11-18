package dev.caseguard.caseapi;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.http.HttpResponse;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;

/**
 * Phase 1.2: behaviour of POST /cases/{id}/escalations and the escalation lookups.
 */
class EscalationApiTest extends ApiTestBase {

  // ---------- creating and replaying ----------

  @Test
  @DisplayName("new request: 202, REQUESTED, one escalation row and one outbox row")
  void newRequestCreatesEscalationAndOutboxEvent() throws Exception {
    UUID key = UUID.randomUUID();
    HttpResponse<String> r = escalate("acme", "cust-A1", "C-1042", key);

    assertThat(r.statusCode()).isEqualTo(202);
    JsonNode b = body(r);
    assertThat(b.get("status").asString()).isEqualTo("REQUESTED");
    assertThat(b.get("caseId").asString()).isEqualTo("C-1042");
    assertThat(b.get("operationId").asString()).isEqualTo(key.toString());
    assertThat(r.headers().firstValue("Location")).hasValue("/escalations/" + b.get("id").asString());

    assertThat(count("select count(*) from escalations")).isEqualTo(1);
    // The outbox row was written in the same transaction, unpublished, with the right payload.
    assertThat(count("select count(*) from outbox where published_at is null")).isEqualTo(1);
    JsonNode payload = json.readTree(jdbc.queryForObject("select payload::text from outbox", String.class));
    assertThat(payload.get("escalationId").asString()).isEqualTo(b.get("id").asString());
    assertThat(payload.get("caseId").asString()).isEqualTo("C-1042");
    assertThat(payload.get("tenantId").asString()).isEqualTo("acme");
  }

  @Test
  @DisplayName("retry with the same key: 200 replay, same escalation, NO second row or event")
  void sameKeyIsReplayed() throws Exception {
    UUID key = UUID.randomUUID();
    HttpResponse<String> first = escalate("acme", "cust-A1", "C-1042", key);
    HttpResponse<String> retry = escalate("acme", "cust-A1", "C-1042", key);

    assertThat(retry.statusCode()).isEqualTo(200);
    assertThat(retry.headers().firstValue("Idempotent-Replayed")).hasValue("true");
    assertThat(body(retry).get("id")).isEqualTo(body(first).get("id"));
    assertThat(count("select count(*) from escalations")).isEqualTo(1);
    assertThat(count("select count(*) from outbox")).isEqualTo(1);
  }

  @Test
  @DisplayName("different key, same case: 409 already_open with the existing id")
  void secondOperationOnSameCaseIsAlreadyOpen() throws Exception {
    HttpResponse<String> first = escalate("acme", "cust-A1", "C-1042", UUID.randomUUID());
    HttpResponse<String> second = escalate("acme", "cust-A1", "C-1042", UUID.randomUUID());

    assertThat(second.statusCode()).isEqualTo(409);
    assertThat(body(second).get("code").asString()).isEqualTo("already_open");
    assertThat(body(second).get("existingEscalationId")).isEqualTo(body(first).get("id"));
    assertThat(count("select count(*) from escalations")).isEqualTo(1);
  }

  @Test
  @DisplayName("same key reused for a different case: 409 operation_conflict")
  void sameKeyDifferentCaseIsConflict() throws Exception {
    UUID key = UUID.randomUUID();
    escalate("acme", "cust-A1", "C-1042", key);
    HttpResponse<String> misuse = escalate("acme", "cust-A1", "C-1050", key);

    assertThat(misuse.statusCode()).isEqualTo(409);
    assertThat(body(misuse).get("code").asString()).isEqualTo("operation_conflict");
    assertThat(count("select count(*) from escalations where case_id = 'C-1050'")).isZero();
  }

  @Test
  @DisplayName("a FAILED escalation does not block a new attempt")
  void failedEscalationAllowsRetryWithNewKey() throws Exception {
    escalate("acme", "cust-A1", "C-1042", UUID.randomUUID());
    jdbc.update("update escalations set status = 'FAILED'");

    HttpResponse<String> again = escalate("acme", "cust-A1", "C-1042", UUID.randomUUID());
    assertThat(again.statusCode()).isEqualTo(202);
    assertThat(count("select count(*) from escalations where status <> 'FAILED'")).isEqualTo(1);
  }

  @Test
  @DisplayName("closed case: 409 case_not_open")
  void closedCaseCannotBeEscalated() throws Exception {
    jdbc.update("update cases set status = 'CLOSED' where id = 'C-1042'");
    HttpResponse<String> r = escalate("acme", "cust-A1", "C-1042", UUID.randomUUID());

    assertThat(r.statusCode()).isEqualTo(409);
    assertThat(body(r).get("code").asString()).isEqualTo("case_not_open");
  }

  // ---------- ownership ----------

  @Test
  @DisplayName("escalating another tenant's case: 404 and nothing written")
  void otherTenantsCaseIs404() throws Exception {
    HttpResponse<String> r = escalate("acme", "cust-A1", "C-2001", UUID.randomUUID());
    assertThat(r.statusCode()).isEqualTo(404);
    assertThat(count("select count(*) from escalations")).isZero();
    assertThat(count("select count(*) from outbox")).isZero();
  }

  @Test
  @DisplayName("someone else's operation id never returns their escalation")
  void foreignOperationIdDoesNotLeak() throws Exception {
    UUID key = UUID.randomUUID();
    HttpResponse<String> a1 = escalate("acme", "cust-A1", "C-1042", key);

    // cust-A2 (same tenant) reuses A1's key on THEIR OWN case.
    HttpResponse<String> a2 = escalate("acme", "cust-A2", "C-1200", key);
    assertThat(a2.statusCode()).isEqualTo(409);
    assertThat(body(a2).get("code").asString()).isEqualTo("operation_conflict");
    assertThat(a2.body()).doesNotContain(body(a1).get("id").asString()).doesNotContain("C-1042");

    // ...and on A1's case: ownership is checked first, so it's a plain 404.
    assertThat(escalate("acme", "cust-A2", "C-1042", key).statusCode()).isEqualTo(404);
  }

  @Test
  @DisplayName("GET /escalations/{id}: owner 200, anyone else 404")
  void getEscalationIsOwnershipChecked() throws Exception {
    String id = body(escalate("acme", "cust-A1", "C-1042", UUID.randomUUID())).get("id").asString();

    HttpResponse<String> mine = httpGet("acme", "cust-A1", "/escalations/" + id);
    assertThat(mine.statusCode()).isEqualTo(200);
    assertThat(body(mine).get("status").asString()).isEqualTo("REQUESTED");

    assertThat(httpGet("globex", "cust-G1", "/escalations/" + id).statusCode()).isEqualTo(404);
    assertThat(httpGet("acme", "cust-A2", "/escalations/" + id).statusCode()).isEqualTo(404);
  }

  @Test
  @DisplayName("GET /escalations?operationId=: finds own operation, empty for others (reconcile step)")
  void findByOperation() throws Exception {
    UUID key = UUID.randomUUID();
    escalate("acme", "cust-A1", "C-1042", key);

    HttpResponse<String> mine = httpGet("acme", "cust-A1", "/escalations?operationId=" + key);
    assertThat(body(mine).size()).isEqualTo(1);

    HttpResponse<String> other = httpGet("acme", "cust-A2", "/escalations?operationId=" + key);
    assertThat(other.statusCode()).isEqualTo(200);
    assertThat(body(other).size()).isZero();

    HttpResponse<String> never = httpGet("acme", "cust-A1", "/escalations?operationId=" + UUID.randomUUID());
    assertThat(body(never).size()).isZero();
  }

  // ---------- input validation ----------

  @Test
  @DisplayName("missing or malformed Idempotency-Key: 400 missing_idempotency_key")
  void idempotencyKeyIsRequired() throws Exception {
    HttpResponse<String> missing = escalate("acme", "cust-A1", "C-1042", null);
    assertThat(missing.statusCode()).isEqualTo(400);
    assertThat(body(missing).get("code").asString()).isEqualTo("missing_idempotency_key");

    HttpResponse<String> notUuid = escalate("acme", "cust-A1", "C-1042", "retry-1",
        "{\"reason\":\"late\",\"policyRef\":\"ESC-3@v2\"}");
    assertThat(notUuid.statusCode()).isEqualTo(400);
    assertThat(count("select count(*) from escalations")).isZero();
  }

  @Test
  @DisplayName("invalid body: 400 invalid_request with the failing fields")
  void bodyIsValidated() throws Exception {
    HttpResponse<String> r = escalate("acme", "cust-A1", "C-1042", UUID.randomUUID().toString(),
        "{\"reason\":\"  \",\"policyRef\":\"whatever\"}");
    assertThat(r.statusCode()).isEqualTo(400);
    JsonNode b = body(r);
    assertThat(b.get("code").asString()).isEqualTo("invalid_request");
    assertThat(b.get("fields").has("reason")).isTrue();
    assertThat(b.get("fields").has("policyRef")).isTrue();
  }
}
