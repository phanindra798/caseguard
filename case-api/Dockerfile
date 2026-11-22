# Multi-stage build: compile with the full JDK, run on a small JRE image.

# ---- build stage ----
FROM eclipse-temurin:21-jdk AS build
WORKDIR /src
# Copy the build files first so dependency downloads are cached between code changes.
COPY .mvn/ .mvn/
COPY mvnw pom.xml ./
RUN ./mvnw -B -q dependency:go-offline
COPY src/ src/
RUN ./mvnw -B -q package -DskipTests
# OpenTelemetry Java agent (Maven Central, checksum verified). Enabled at run time via JAVA_TOOL_OPTIONS.
ARG OTEL_AGENT_VERSION=2.34.0
RUN URL=https://repo1.maven.org/maven2/io/opentelemetry/javaagent/opentelemetry-javaagent/${OTEL_AGENT_VERSION}/opentelemetry-javaagent-${OTEL_AGENT_VERSION}.jar \
 && curl -sSfL "$URL" -o /src/opentelemetry-javaagent.jar \
 && echo "$(curl -sSfL "$URL.sha1")  /src/opentelemetry-javaagent.jar" | sha1sum -c -

# ---- runtime stage ----
FROM eclipse-temurin:21-jre
WORKDIR /app
# Run as a non-root user.
RUN useradd --system --uid 10001 caseguard
COPY --from=build /src/opentelemetry-javaagent.jar /app/opentelemetry-javaagent.jar
COPY --from=build /src/target/case-api-*.jar app.jar
USER caseguard
EXPOSE 8081
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
