package dev.caseguard.caseapi.escalations;

/**
 * Outcome of a request: a brand-new escalation (HTTP 202) or a replay of one
 * this same operation already created (HTTP 200).
 */
public record EscalationResult(Escalation escalation, boolean created) {

  static EscalationResult created(Escalation e) {
    return new EscalationResult(e, true);
  }

  static EscalationResult replayed(Escalation e) {
    return new EscalationResult(e, false);
  }
}
