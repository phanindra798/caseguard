package dev.caseguard.caseapi.approvals;

/** Errors of the approval flow. Each maps to a stable `code` in ApiExceptionHandler. */
public final class ApprovalExceptions {

  private ApprovalExceptions() {
  }

  /** 409 case_changed: the case is not in the version the caller based its decision on. */
  public static class CaseChangedException extends RuntimeException {
    public CaseChangedException(int expected, int actual) {
      super("case changed: expected version " + expected + ", current is " + actual);
    }
  }

  /** 409 already_decided: a supervisor can decide an approval only once. */
  public static class AlreadyDecidedException extends RuntimeException {
    public AlreadyDecidedException(String status) {
      super("approval already " + status);
    }
  }

  /** 409 approval_expired. */
  public static class ApprovalExpiredException extends RuntimeException {
    public ApprovalExpiredException() {
      super("approval request has expired");
    }
  }

  /**
   * 403 approval_required: an escalation over the limit without a VALID approval.
   * {@code why} says which binding failed, e.g. "missing", "rejected", "case_changed".
   */
  public static class ApprovalRequiredException extends RuntimeException {
    private final String why;

    public ApprovalRequiredException(String why) {
      super("a matching approved approval is required (" + why + ")");
      this.why = why;
    }

    public String getWhy() {
      return why;
    }
  }
}
