package dev.caseguard.caseapi;

import static org.assertj.core.api.Assertions.assertThat;

import java.net.URI;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.SerializationFeature;
import tools.jackson.databind.node.ObjectNode;

/**
 * Contract test: the API the server actually exposes must match the committed contract
 * file {@code case-api/openapi.json}. The Python agent's tool layer is built against that file,
 * so an accidental API change fails the build HERE, not later in the agent.
 *
 * <p>After an intentional API change, regenerate the file:
 * {@code UPDATE_CONTRACT=true mvn test -Dtest=ApiContractTest}, then review the diff in Git.
 */
class ApiContractTest extends ApiTestBase {

  private static final Path CONTRACT = Path.of("openapi.json");

  @Test
  @DisplayName("served OpenAPI spec matches the committed contract")
  void contractMatches() throws Exception {
    HttpResponse<String> r = http.send(
        HttpRequest.newBuilder(URI.create("http://localhost:" + port + "/v3/api-docs")).GET().build(),
        HttpResponse.BodyHandlers.ofString());
    assertThat(r.statusCode()).isEqualTo(200);

    ObjectNode served = (ObjectNode) json.readTree(r.body());
    served.remove("servers");   // contains the random test port; not part of the contract

    if ("true".equals(System.getenv("UPDATE_CONTRACT")) || !Files.exists(CONTRACT)) {
      Files.writeString(CONTRACT, json.writer().with(SerializationFeature.INDENT_OUTPUT)
          .writeValueAsString(served) + "\n");
    }

    JsonNode committed = json.readTree(Files.readString(CONTRACT));
    assertThat(served)
        .as("API changed but openapi.json was not updated. Run with UPDATE_CONTRACT=true and review the diff.")
        .isEqualTo(committed);

    // The endpoints the agent depends on must exist.
    assertThat(served.get("paths").has("/cases/{caseId}/escalations")).isTrue();
    assertThat(served.get("paths").has("/escalations/{escalationId}")).isTrue();
    assertThat(served.get("paths").has("/escalations")).isTrue();
  }
}
