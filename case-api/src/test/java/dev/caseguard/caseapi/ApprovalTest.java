package dev.caseguard.caseapi;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.http.HttpResponse;
import java.util.UUID;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;

/**
 * Phase 2.1: supervisor approvals, and the backend refusing expensive escalations without a
 * VALID one. Order 1099 (case C-1099) is $750, over the $500 limit.
 */
class ApprovalTest extends ApiTestBase {

  private static final String[] ASHA = {"X-Tenant-Id", "acme", "X-Customer-Id", "cust-A1"};
  private static final String[] ACME_SUP = {"X-Tenant-Id", "acme", "X-Supervisor-Id", "sup-maria"};
  private static final String[] GLOBEX_SUP = {"X-Tenant-Id", "globex", "X-Supervisor-Id", "sup-gary"};

  private static String[] with(String[] base, String... more) {
    String[] all = new String[base.length + more.length];
    System.arraycopy(base, 0, all, 0, base.length);
    System.arraycopy(more, 0, all, base.length, more.length);
    return all;
  }

  private HttpResponse<String> requestApproval(UUID op, String caseId, int caseVersion) throws Exception {
    return send("POST", "/cases/" + caseId + "/approvals",
        "{\"caseVersion\":" + caseVersion + ",\"policyRef\":\"ESC-3@v2\",\"reason\":\"tent 12 days late\","
            + "\"conversationRef\":\"conv-1\"}",
        with(ASHA, "Idempotency-Key", op.toString()));
  }

  private HttpResponse<String> decide(String approvalId, String decision, String[] supervisor) throws Exception {
    return send("POST", "/supervisor/approvals/" + approvalId + "/decision",
        "{\"decision\":\"" + decision + "\"}", supervisor);
  }

  private HttpResponse<String> escalate(UUID op, String caseId, String approvalId) throws Exception {
    String approval = approvalId == null ? "" : ",\"approvalId\":\"" + approvalId + "\"";
    return send("POST", "/cases/" + caseId + "/escalations",
        "{\"reason\":\"late\",\"policyRef\":\"ESC-3@v2\"" + approval + "}",
        with(ASHA, "Idempotency-Key", op.toString()));
  }

  private String approvedFor(UUID op) throws Exception {
    String id = body(requestApproval(op, "C-1099", 0)).get("id").asString();
    assertThat(decide(id, "APPROVED", ACME_SUP).statusCode()).isEqualTo(200);
    return id;
  }

  // ------------------------------------------------------------------ requesting

  @Test
  @DisplayName("request: 201 PENDING, bound to operation, case version and policy; same key replays")
  void requestAndReplay() throws Exception {
    UUID op = UUID.randomUUID();
    HttpResponse<String> first = requestApproval(op, "C-1099", 0);
    assertThat(first.statusCode()).isEqualTo(201);
    JsonNode a = body(first);
    assertThat(a.get("status").asString()).isEqualTo("PENDING");
    assertThat(a.get("operationId").asString()).isEqualTo(op.toString());
    assertThat(a.get("caseVersion").asInt()).isZero();
    assertThat(a.get("policyRef").asString()).isEqualTo("ESC-3@v2");

    HttpResponse<String> again = requestApproval(op, "C-1099", 0);
    assertThat(again.statusCode()).isEqualTo(200);
    assertThat(body(again).get("id")).isEqualTo(a.get("id"));
    assertThat(count("select count(*) from approvals")).isEqualTo(1);
  }

  @Test
  @DisplayName("request based on an outdated case version: 409 case_changed")
  void staleCaseVersionIsRefused() throws Exception {
    jdbc.update("update cases set version = version + 1 where id = 'C-1099'");
    HttpResponse<String> r = requestApproval(UUID.randomUUID(), "C-1099", 0);
    assertThat(r.statusCode()).isEqualTo(409);
    assertThat(body(r).get("code").asString()).isEqualTo("case_changed");
  }

  @Test
  @DisplayName("another customer's case: 404")
  void otherCustomersCaseIs404() throws Exception {
    HttpResponse<String> r = requestApproval(UUID.randomUUID(), "C-1200", 0);
    assertThat(r.statusCode()).isEqualTo(404);
  }

  // ------------------------------------------------------------------ supervisors

  @Test
  @DisplayName("supervisors only see and decide their OWN tenant's approvals, and only once")
  void supervisorScopeAndSingleDecision() throws Exception {
    String id = body(requestApproval(UUID.randomUUID(), "C-1099", 0)).get("id").asString();

    assertThat(body(send("GET", "/supervisor/approvals", null, ACME_SUP)).size()).isEqualTo(1);
    assertThat(body(send("GET", "/supervisor/approvals", null, GLOBEX_SUP)).size()).isZero();
    assertThat(decide(id, "APPROVED", GLOBEX_SUP).statusCode()).as("other tenant").isEqualTo(404);

    HttpResponse<String> ok = decide(id, "APPROVED", ACME_SUP);
    assertThat(ok.statusCode()).isEqualTo(200);
    assertThat(body(ok).get("decidedBy").asString()).isEqualTo("sup-maria");

    HttpResponse<String> twice = decide(id, "REJECTED", ACME_SUP);
    assertThat(twice.statusCode()).isEqualTo(409);
    assertThat(body(twice).get("code").asString()).isEqualTo("already_decided");
  }

  @Test
  @DisplayName("an expired request can't be decided")
  void expiredCannotBeDecided() throws Exception {
    String id = body(requestApproval(UUID.randomUUID(), "C-1099", 0)).get("id").asString();
    jdbc.update("update approvals set expires_at = now() - interval '1 minute'");
    HttpResponse<String> r = decide(id, "APPROVED", ACME_SUP);
    assertThat(r.statusCode()).isEqualTo(409);
    assertThat(body(r).get("code").asString()).isEqualTo("approval_expired");
  }

  @Test
  @DisplayName("supervisor endpoints need the supervisor header; invalid decisions are rejected")
  void supervisorInputValidation() throws Exception {
    assertThat(send("GET", "/supervisor/approvals", null, "X-Tenant-Id", "acme").statusCode()).isEqualTo(400);
    String id = body(requestApproval(UUID.randomUUID(), "C-1099", 0)).get("id").asString();
    assertThat(decide(id, "MAYBE", ACME_SUP).statusCode()).isEqualTo(400);
  }

  // ------------------------------------------------------------------ backend enforcement

  private void assertRefused(HttpResponse<String> r, String why) {
    assertThat(r.statusCode()).isEqualTo(403);
    assertThat(body(r).get("code").asString()).isEqualTo("approval_required");
    assertThat(body(r).get("why").asString()).isEqualTo(why);
  }

  @Test
  @DisplayName("expensive order without an approval: 403 approval_required (missing), nothing written")
  void expensiveOrderNeedsApproval() throws Exception {
    assertRefused(escalate(UUID.randomUUID(), "C-1099", null), "missing");
    assertThat(count("select count(*) from escalations")).isZero();
    assertThat(count("select count(*) from outbox")).isZero();
  }

  @Test
  @DisplayName("pending or rejected approvals don't count")
  void pendingOrRejectedDoNotCount() throws Exception {
    UUID op = UUID.randomUUID();
    String id = body(requestApproval(op, "C-1099", 0)).get("id").asString();
    assertRefused(escalate(op, "C-1099", id), "pending");
    decide(id, "REJECTED", ACME_SUP);
    assertRefused(escalate(op, "C-1099", id), "rejected");
  }

  @Test
  @DisplayName("an approval for a DIFFERENT operation doesn't count")
  void approvalIsBoundToTheOperation() throws Exception {
    String id = approvedFor(UUID.randomUUID());
    assertRefused(escalate(UUID.randomUUID(), "C-1099", id), "wrong_operation");
  }

  @Test
  @DisplayName("the case changed after approval: the old approval doesn't count")
  void approvalIsBoundToTheCaseVersion() throws Exception {
    UUID op = UUID.randomUUID();
    String id = approvedFor(op);
    jdbc.update("update cases set version = version + 1 where id = 'C-1099'");
    assertRefused(escalate(op, "C-1099", id), "case_changed");
  }

  @Test
  @DisplayName("a different policy version than the one approved doesn't count")
  void approvalIsBoundToThePolicy() throws Exception {
    UUID op = UUID.randomUUID();
    String id = approvedFor(op);
    HttpResponse<String> r = send("POST", "/cases/C-1099/escalations",
        "{\"reason\":\"late\",\"policyRef\":\"ESC-3@v3\",\"approvalId\":\"" + id + "\"}",
        with(ASHA, "Idempotency-Key", op.toString()));
    assertRefused(r, "policy_changed");
  }

  @Test
  @DisplayName("an expired approval doesn't count")
  void expiredApprovalDoesNotCount() throws Exception {
    UUID op = UUID.randomUUID();
    String id = approvedFor(op);
    jdbc.update("update approvals set expires_at = now() - interval '1 minute'");
    assertRefused(escalate(op, "C-1099", id), "expired");
  }

  @Test
  @DisplayName("matching APPROVED approval: 202, escalation and outbox event written")
  void validApprovalAllowsTheEscalation() throws Exception {
    UUID op = UUID.randomUUID();
    String id = approvedFor(op);
    HttpResponse<String> r = escalate(op, "C-1099", id);
    assertThat(r.statusCode()).isEqualTo(202);
    assertThat(count("select count(*) from escalations where case_id = 'C-1099'")).isEqualTo(1);
    assertThat(count("select count(*) from outbox")).isEqualTo(1);
  }

  @Test
  @DisplayName("another customer's approval id can't be used")
  void foreignApprovalIdIsRejected() throws Exception {
    String id = approvedFor(UUID.randomUUID());
    jdbc.update("update approvals set customer_id = 'cust-A2'");   // pretend it belongs to someone else
    assertRefused(escalate(UUID.randomUUID(), "C-1099", id), "not_found");
  }
}
