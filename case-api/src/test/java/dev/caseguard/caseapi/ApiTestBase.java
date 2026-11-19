package dev.caseguard.caseapi;

import dev.caseguard.caseapi.escalations.EscalationController;
import dev.caseguard.caseapi.identity.CallerIdentity;
import dev.caseguard.caseapi.reliability.FailPoints;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.UUID;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.context.annotation.Import;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * Shared setup for all API tests: a real server on a random port, a real PostgreSQL,
 * and a clean slate of escalation data before every test.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
@Import(FreshDatabaseConfig.class)
public abstract class ApiTestBase {

  @LocalServerPort protected int port;
  @Autowired protected JdbcTemplate jdbc;
  @Autowired protected JsonMapper json;
  @Autowired protected FailPoints failPoints;

  protected final HttpClient http = HttpClient.newHttpClient();

  @BeforeEach
  void resetData() {
    jdbc.update("delete from follow_up_tasks");
    jdbc.update("delete from processed_events");
    jdbc.update("delete from outbox");
    jdbc.update("delete from escalations");
    jdbc.update("delete from approvals");
    jdbc.update("update cases set status = 'OPEN', version = 0");   // some tests bump the version
  }

  @AfterEach
  void disarmFailPoints() {
    failPoints.disarm();
  }

  // ---------- HTTP helpers ----------

  protected HttpResponse<String> httpGet(String tenant, String customer, String path) throws Exception {
    HttpRequest req = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
        .header(CallerIdentity.TENANT_HEADER, tenant)
        .header(CallerIdentity.CUSTOMER_HEADER, customer)
        .GET().build();
    return http.send(req, HttpResponse.BodyHandlers.ofString());
  }

  protected HttpResponse<String> escalate(String tenant, String customer, String caseId, UUID key)
      throws Exception {
    return escalate(tenant, customer, caseId, key == null ? null : key.toString(),
        "{\"reason\":\"Order is 7 business days late\",\"policyRef\":\"ESC-3@v2\"}");
  }

  protected HttpResponse<String> escalate(String tenant, String customer, String caseId,
                                          String key, String body) throws Exception {
    HttpRequest.Builder b = HttpRequest.newBuilder(
            URI.create("http://localhost:" + port + "/cases/" + caseId + "/escalations"))
        .header(CallerIdentity.TENANT_HEADER, tenant)
        .header(CallerIdentity.CUSTOMER_HEADER, customer)
        .header("Content-Type", "application/json")
        .POST(HttpRequest.BodyPublishers.ofString(body));
    if (key != null) {
      b.header(EscalationController.IDEMPOTENCY_HEADER, key);
    }
    return http.send(b.build(), HttpResponse.BodyHandlers.ofString());
  }

  protected HttpResponse<String> send(String method, String path, String body, String... headers) throws Exception {
    HttpRequest.Builder b = HttpRequest.newBuilder(URI.create("http://localhost:" + port + path))
        .header("Content-Type", "application/json")
        .method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofString(body));
    for (int i = 0; i < headers.length; i += 2) {
      b.header(headers[i], headers[i + 1]);
    }
    return http.send(b.build(), HttpResponse.BodyHandlers.ofString());
  }

  protected JsonNode body(HttpResponse<String> r) {
    return json.readTree(r.body());
  }

  protected int count(String sql, Object... args) {
    return jdbc.queryForObject(sql, Integer.class, args);
  }
}
