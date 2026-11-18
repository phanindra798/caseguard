package dev.caseguard.worker;

import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

/**
 * Named spots where a test can force a failure. Same idea as in the Case API.
 *
 * <ul>
 *   <li>{@code FAIL_POINT=<name>} env var: HALT the JVM the first time that spot is reached
 *       (a real crash, used by scripts/demo-crash-replay.sh).</li>
 *   <li>{@link #armOnce(String, Action)} in JUnit: fail exactly ONCE, so the retry after it can
 *       show recovery.</li>
 * </ul>
 */
@Component
public class FailPoints {

  public enum Action { HALT, THROW }

  private volatile String armedName;
  private volatile Action armedAction;
  private final AtomicInteger remaining = new AtomicInteger();

  public FailPoints(@Value("${FAIL_POINT:}") String fromEnv) {
    if (!fromEnv.isBlank()) {
      armOnce(fromEnv, Action.HALT);
    }
  }

  public void hit(String name) {
    if (!name.equals(armedName) || remaining.getAndDecrement() <= 0) {
      return;
    }
    if (armedAction == Action.HALT) {
      System.err.println("FAIL_POINT triggered (halt): " + name);
      Runtime.getRuntime().halt(1);   // like a power cut: no shutdown hooks, no offset commit
    }
    throw new FailPointException(name);
  }

  public void armOnce(String name, Action action) {
    remaining.set(1);
    armedAction = action;
    armedName = name;
  }

  public void disarm() {
    armedName = null;
    remaining.set(0);
  }

  public static class FailPointException extends RuntimeException {
    public FailPointException(String name) {
      super("fail point triggered: " + name);
    }
  }
}
