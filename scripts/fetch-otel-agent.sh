#!/usr/bin/env bash
# Download the OpenTelemetry Java agent from Maven Central into tools/ (git-ignored) and verify it.
# The agent instruments Spring MVC, JDBC and Kafka automatically, including trace propagation
# through Kafka message headers.
set -euo pipefail
VERSION="${OTEL_AGENT_VERSION:-2.34.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/tools/opentelemetry-javaagent.jar"
URL="https://repo1.maven.org/maven2/io/opentelemetry/javaagent/opentelemetry-javaagent/$VERSION/opentelemetry-javaagent-$VERSION.jar"
mkdir -p "$ROOT/tools"
curl -sSfL "$URL" -o "$DEST.tmp"
EXPECTED="$(curl -sSfL "$URL.sha1")"
ACTUAL="$(sha1sum "$DEST.tmp" | cut -d' ' -f1)"
[ "$EXPECTED" = "$ACTUAL" ] || { echo "checksum mismatch for $URL"; rm -f "$DEST.tmp"; exit 1; }
mv "$DEST.tmp" "$DEST"
echo "OpenTelemetry Java agent $VERSION -> $DEST (sha1 verified)"
