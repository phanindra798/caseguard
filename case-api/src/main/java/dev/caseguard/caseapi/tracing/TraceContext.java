package dev.caseguard.caseapi.tracing;

import io.opentelemetry.api.GlobalOpenTelemetry;
import io.opentelemetry.api.trace.Span;
import io.opentelemetry.api.trace.Tracer;
import io.opentelemetry.api.trace.propagation.W3CTraceContextPropagator;
import io.opentelemetry.context.Context;
import io.opentelemetry.context.propagation.TextMapGetter;
import java.util.HashMap;
import java.util.Map;

/**
 * Carries a trace across the outbox. Uses only the OpenTelemetry API: with the OpenTelemetry Java
 * agent attached it records real spans; without it every call is a cheap no-op.
 */
public final class TraceContext {

  private static final W3CTraceContextPropagator W3C = W3CTraceContextPropagator.getInstance();

  private static final TextMapGetter<Map<String, String>> GETTER = new TextMapGetter<>() {
    @Override
    public Iterable<String> keys(Map<String, String> carrier) {
      return carrier.keySet();
    }

    @Override
    public String get(Map<String, String> carrier, String key) {
      return carrier == null ? null : carrier.get(key);
    }
  };

  private TraceContext() {
  }

  /** The current W3C traceparent, or null when no trace is active (e.g. no agent attached). */
  public static String currentTraceParent() {
    Map<String, String> carrier = new HashMap<>();
    W3C.inject(Context.current(), carrier, Map::put);
    return carrier.get("traceparent");
  }

  /**
   * Start a span that continues the trace stored in {@code traceParent}. The caller makes it current
   * so that the Kafka producer instrumentation sends the trace on to the consumer.
   */
  public static Span startContinuing(String traceParent, String spanName) {
    Context parent = traceParent == null ? Context.current()
        : W3C.extract(Context.root(), Map.of("traceparent", traceParent), GETTER);
    Tracer tracer = GlobalOpenTelemetry.getTracer("caseguard-case-api");
    return tracer.spanBuilder(spanName).setParent(parent).startSpan();
  }
}
