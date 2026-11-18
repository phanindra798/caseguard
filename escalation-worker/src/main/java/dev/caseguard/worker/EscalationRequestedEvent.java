package dev.caseguard.worker;

import java.util.UUID;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.json.JsonMapper;

/**
 * The parts of an EscalationRequested envelope the worker needs.
 *
 * <pre>
 * { "eventId": "...", "eventType": "EscalationRequested", "occurredAt": "...",
 *   "payload": { "escalationId": "...", "tenantId": "...", "caseId": "...", ... } }
 * </pre>
 */
public record EscalationRequestedEvent(UUID eventId, UUID escalationId, String tenantId, String caseId) {

  public static final String TYPE = "EscalationRequested";

  /** Parse and validate. Anything malformed is a poison message, not something to retry. */
  public static EscalationRequestedEvent parse(String json, JsonMapper mapper) {
    try {
      JsonNode root = mapper.readTree(json);
      if (!TYPE.equals(text(root, "eventType"))) {
        throw new InvalidEventException("unexpected eventType: " + text(root, "eventType"));
      }
      JsonNode p = root.get("payload");
      if (p == null || !p.isObject()) {
        throw new InvalidEventException("missing payload");
      }
      return new EscalationRequestedEvent(
          UUID.fromString(required(root, "eventId")),
          UUID.fromString(required(p, "escalationId")),
          required(p, "tenantId"),
          required(p, "caseId"));
    } catch (InvalidEventException e) {
      throw e;
    } catch (RuntimeException e) {   // bad JSON, bad UUID, ...
      throw new InvalidEventException("unparseable event: " + e.getMessage());
    }
  }

  private static String text(JsonNode n, String field) {
    JsonNode v = n.get(field);
    return v == null || v.isNull() ? null : v.asString();
  }

  private static String required(JsonNode n, String field) {
    String v = text(n, field);
    if (v == null || v.isBlank()) {
      throw new InvalidEventException("missing field: " + field);
    }
    return v;
  }
}
