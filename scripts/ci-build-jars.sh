#!/usr/bin/env bash
# Build both service jars for CI, retrying dependency downloads.
# Why: Maven Central throttles shared CI runners; when several jobs download the same packages at
# once, a download can fail ("Could not transfer artifact"). That says nothing about our code, so
# we retry with growing pauses instead of failing the build. -U re-checks downloads that a failed
# earlier attempt marked as missing in the local cache.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

retry() {
  local attempt
  for attempt in 1 2 3 4; do
    "$@" && return 0
    echo "::warning::attempt $attempt failed: $*; retrying in $((attempt * 20))s"
    sleep $((attempt * 20))
  done
  return 1
}

for svc in case-api escalation-worker; do
  (cd "$ROOT/$svc" && retry ./mvnw -B -q -U dependency:go-offline && retry ./mvnw -B -q package -DskipTests)
done
