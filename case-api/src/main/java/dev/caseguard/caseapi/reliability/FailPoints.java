package dev.caseguard.caseapi.reliability;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Named spots in the code where a test can force a failure, to prove what happens
 * if the process dies or errors at exactly that moment.
 *
 * <p>Off by default. Turned on by:
 * <ul>
 *   <li>env/property {@code FAIL_POINT=<name>} for whole-process crash tests (harness, Phase 1.5), or</li>
 *   <li>{@link #arm(String, Action)} from inside a JUnit test.</li>
 * </ul>
 *
 * <p>Two actions:
 * <ul>
 *   <li>HALT: {@code Runtime.halt(1)}, like a power cut. No shutdown hooks, no cleanup.</li>
 *   <li>THROW: an exception, so a test in the same JVM can observe the rollback.</li>
 * </ul>
 */
@Component
public class FailPoints {

  public enum Action { HALT, THROW }

  private volatile String armedName;
  private volatile Action armedAction;

  public FailPoints(@Value("${FAIL_POINT:}") String fromEnv) {
    if (!fromEnv.isBlank()) {
      this.armedName = fromEnv;
      this.armedAction = Action.HALT;
    }
  }

  /** Call this at a named spot. Does nothing unless that name is armed. */
  public void hit(String name) {
    if (!name.equals(armedName)) {
      return;
    }
    if (armedAction == Action.HALT) {
      System.err.println("FAIL_POINT triggered (halt): " + name);
      Runtime.getRuntime().halt(1);
    }
    throw new FailPointException(name);
  }

  public void arm(String name, Action action) {
    this.armedAction = action;
    this.armedName = name;
  }

  public void disarm() {
    this.armedName = null;
  }

  public static class FailPointException extends RuntimeException {
    public FailPointException(String name) {
      super("fail point triggered: " + name);
    }
  }
}
