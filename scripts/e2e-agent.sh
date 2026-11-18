#!/usr/bin/env bash
# End-to-end: start the real Case API + worker on a FRESH database and topic, then run the
# agent's e2e tests against them.
#
# Needs: PostgreSQL on localhost:5432 (user/password caseguard, CREATEDB), Kafka on localhost:9092,
# Java 21, and the agent's virtualenv (cd agent && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]").
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ID="$(date +%s)"
DB="caseguard_e2e"
PORT=8092
LOGS="${TMPDIR:-/tmp}/caseguard-e2e-$RUN_ID"
mkdir -p "$LOGS"
export PGPASSWORD=caseguard PGHOST=localhost PGUSER=caseguard
PY="${PYTHON:-$ROOT/agent/.venv/bin/python}"

API_JAR="$ROOT/case-api/target/case-api-0.1.0-SNAPSHOT.jar"
WORKER_JAR="$ROOT/escalation-worker/target/escalation-worker-0.1.0-SNAPSHOT.jar"
[ -f "$API_JAR" ]    || (cd "$ROOT/case-api" && ./mvnw -q -B package -DskipTests)
[ -f "$WORKER_JAR" ] || (cd "$ROOT/escalation-worker" && ./mvnw -q -B package -DskipTests)

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

dropdb --if-exists "$DB" 2>/dev/null; createdb "$DB"
ENV=(DB_URL="jdbc:postgresql://localhost:5432/$DB" KAFKA_BOOTSTRAP=localhost:9092
     CASEGUARD_TOPICS_ESCALATION_REQUESTED="escalation.requested.e2e-$RUN_ID")

env "${ENV[@]}" PORT=$PORT java -jar "$API_JAR" >"$LOGS/case-api.log" 2>&1 & PIDS+=($!)
for _ in $(seq 1 60); do curl -sf "localhost:$PORT/actuator/health" >/dev/null && break; sleep 1; done
curl -sf "localhost:$PORT/actuator/health" >/dev/null || { echo "case-api failed, see $LOGS"; exit 1; }
env "${ENV[@]}" SPRING_KAFKA_CONSUMER_GROUP_ID="worker-e2e-$RUN_ID" java -jar "$WORKER_JAR" \
  >"$LOGS/worker.log" 2>&1 & PIDS+=($!)

cd "$ROOT/agent"
CASEGUARD_E2E=1 CASE_API_URL="http://localhost:$PORT" "$PY" -m pytest -m e2e -v -p no:cacheprovider "$@"
echo "logs: $LOGS"
