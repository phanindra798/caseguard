#!/usr/bin/env bash
# Real crash-and-replay demo: kills a JVM at the worst possible moment, restarts it, and proves
# the end state is still exactly ONE follow-up task.
#
#   scripts/demo-crash-replay.sh worker   # worker dies AFTER its DB commit, BEFORE acking Kafka
#   scripts/demo-crash-replay.sh relay    # relay dies AFTER publishing, BEFORE marking the outbox row
#
# Needs: PostgreSQL on localhost:5432 (user/password caseguard, CREATEDB), Kafka on localhost:9092,
# Java 21, psql, curl. With Docker: `make infra` starts PostgreSQL and Kafka.
# Exits non-zero if the final state is wrong, so CI runs it too.
set -euo pipefail

SCENARIO="${1:-worker}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ID="$(date +%s)"
DB="caseguard_demo"
TOPIC="escalation.requested.demo-$RUN_ID"      # fresh topic: no leftovers from earlier runs
GROUP="escalation-worker-demo-$RUN_ID"
API_PORT=8091
LOGS="${TMPDIR:-/tmp}/caseguard-demo-$RUN_ID"
mkdir -p "$LOGS"
export PGPASSWORD=caseguard PGHOST=localhost PGUSER=caseguard

API_JAR="$ROOT/case-api/target/case-api-0.1.0-SNAPSHOT.jar"
WORKER_JAR="$ROOT/escalation-worker/target/escalation-worker-0.1.0-SNAPSHOT.jar"

say()  { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[1;32m✓ %s\033[0m\n' "$*"; }
fail() { printf '  \033[1;31m✗ %s\033[0m\n' "$*"; exit 1; }
sql()  { psql -d "$DB" -tAc "$1"; }

PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

common_env=(
  DB_URL="jdbc:postgresql://localhost:5432/$DB"
  KAFKA_BOOTSTRAP=localhost:9092
  CASEGUARD_TOPICS_ESCALATION_REQUESTED="$TOPIC"
)

start_api() {   # $1 = FAIL_POINT (may be empty), $2 = log name
  env "${common_env[@]}" PORT=$API_PORT FAIL_POINT="$1" java -jar "$API_JAR" >"$LOGS/$2.log" 2>&1 &
  API_PID=$!; PIDS+=("$API_PID")
  for _ in $(seq 1 60); do curl -sf "localhost:$API_PORT/actuator/health" >/dev/null && return; sleep 1; done
  fail "case-api did not start (see $LOGS/$2.log)"
}

start_worker() {   # $1 = FAIL_POINT (may be empty), $2 = log name
  env "${common_env[@]}" SPRING_KAFKA_CONSUMER_GROUP_ID="$GROUP" FAIL_POINT="$1" \
    java -jar "$WORKER_JAR" >"$LOGS/$2.log" 2>&1 &
  WORKER_PID=$!; PIDS+=("$WORKER_PID")
}

wait_for() {   # $1 = description, $2 = shell condition, $3 = timeout seconds
  for _ in $(seq 1 "${3:-60}"); do eval "$2" && return 0; sleep 1; done
  fail "timed out waiting for: $1"
}

# ---------------------------------------------------------------------------------------------
say "Build (skipped if the jars exist)"
[ -f "$API_JAR" ]    || (cd "$ROOT/case-api" && ./mvnw -q -B package -DskipTests)
[ -f "$WORKER_JAR" ] || (cd "$ROOT/escalation-worker" && ./mvnw -q -B package -DskipTests)
ok "jars ready"

say "Fresh database '$DB' and fresh topic '$TOPIC'"
dropdb --if-exists "$DB"; createdb "$DB"
ok "database created (the Case API runs the migrations on startup)"

escalate() {
  curl -s -o "$LOGS/escalate.json" -w '%{http_code}' -X POST "localhost:$API_PORT/cases/C-1042/escalations" \
    -H "X-Tenant-Id: acme" -H "X-Customer-Id: cust-A1" -H "Content-Type: application/json" \
    -H "Idempotency-Key: $(cat /proc/sys/kernel/random/uuid)" \
    -d '{"reason":"Order is 8 business days late","policyRef":"ESC-3@v2"}'
}

if [ "$SCENARIO" = "worker" ]; then
  # -------------------------------------------------------------------------------------------
  say "Start case-api, and the worker armed to CRASH after its DB commit, before the Kafka ack"
  start_api "" api
  start_worker WORKER_AFTER_DB_COMMIT_BEFORE_ACK worker-1
  ok "worker-1 running with FAIL_POINT=WORKER_AFTER_DB_COMMIT_BEFORE_ACK"

  say "Customer escalation request"
  [ "$(escalate)" = "202" ] && ok "202 Accepted" || fail "escalation request failed"

  say "Wait for worker-1 to die"
  set +e; wait "$WORKER_PID"; CODE=$?; set -e
  [ "$CODE" -ne 0 ] && ok "worker-1 HALTED (exit code $CODE): $(grep -o 'FAIL_POINT triggered.*' "$LOGS/worker-1.log")" \
                     || fail "worker-1 did not crash"

  say "State right after the crash"
  echo "  follow_up_tasks  = $(sql 'select count(*) from follow_up_tasks')"
  echo "  processed_events = $(sql 'select count(*) from processed_events')"
  echo "  escalation       = $(sql 'select status from escalations')"
  echo "  → the work was COMMITTED, but Kafka never got the ack, so it will deliver the event again."

  say "Restart the worker (no fail point)"
  start_worker "" worker-2
  wait_for "redelivered event to be skipped" "grep -q 'duplicate event' '$LOGS/worker-2.log'" 90
  ok "worker-2 got the SAME event again: $(grep -o 'duplicate event.*' "$LOGS/worker-2.log" | head -1)"

else
  # -------------------------------------------------------------------------------------------
  say "Start the worker, and case-api armed to CRASH after publishing, before marking the outbox row"
  start_worker "" worker
  start_api RELAY_AFTER_PUBLISH_BEFORE_MARK api-1
  ok "case-api-1 running with FAIL_POINT=RELAY_AFTER_PUBLISH_BEFORE_MARK"

  say "Customer escalation request"
  [ "$(escalate)" = "202" ] && ok "202 Accepted" || fail "escalation request failed"

  say "Wait for case-api-1 to die"
  set +e; wait "$API_PID"; CODE=$?; set -e
  [ "$CODE" -ne 0 ] && ok "case-api-1 HALTED (exit code $CODE)" || fail "case-api-1 did not crash"
  wait_for "worker to process the first copy" "[ \"\$(sql 'select count(*) from follow_up_tasks')\" = 1 ]" 60

  say "State right after the crash"
  echo "  outbox unpublished = $(sql 'select count(*) from outbox where published_at is null')"
  echo "  follow_up_tasks    = $(sql 'select count(*) from follow_up_tasks')"
  echo "  → the event reached Kafka and the worker, but the 'published' mark was lost."

  say "Restart case-api (no fail point): the relay sends the SAME event again"
  start_api "" api-2
  wait_for "outbox row to be marked published" "[ \"\$(sql 'select count(*) from outbox where published_at is null')\" = 0 ]" 60
  wait_for "worker to skip the re-sent copy" "grep -q 'duplicate event' '$LOGS/worker.log'" 60
  ok "worker skipped the duplicate: $(grep -o 'duplicate event.*' "$LOGS/worker.log" | head -1)"
fi

# ---------------------------------------------------------------------------------------------
say "Final check"
TASKS=$(sql 'select count(*) from follow_up_tasks')
PROCESSED=$(sql 'select count(*) from processed_events')
STATUS=$(sql 'select status from escalations')
echo "  follow_up_tasks  = $TASKS"
echo "  processed_events = $PROCESSED"
echo "  escalation       = $STATUS"
[ "$TASKS" = 1 ] && [ "$PROCESSED" = 1 ] && [ "$STATUS" = COMPLETED ] \
  && ok "PASS: delivered twice, exactly ONE task (scenario: $SCENARIO)" \
  || fail "FAIL: expected 1 task, 1 processed event, COMPLETED"
echo "  logs: $LOGS"
