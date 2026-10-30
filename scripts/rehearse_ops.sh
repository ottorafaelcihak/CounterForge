#!/usr/bin/env bash
# Phase 09 Task 4 disposable operational rehearsal. Root-run only.
#
# Owns one unique Compose project (counterforge-phase09-ops-<timestamp>-<pid>) with distinct
# loopback ports and project-scoped volumes. It never reads .env, never
# touches the default stack or other containers, makes no provider calls
# (PROVIDER_MODE=FIXTURE, empty key, RUN_META_LIVE_TESTS=0), and cleans up
# only after verifying the project label on its own containers.
#
# Usage: scripts/rehearse_ops.sh [--keep]   (KEEP=1 also keeps the stack)
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "${1:-}" = "--keep" ]; then KEEP=1; fi
# Fresh project per run: a rerun can never own or delete a prior --keep stack.
PROJECT="counterforge-phase09-ops-$(date +%Y%m%d%H%M%S)-$$"
export POSTGRES_PASSWORD="phase09-ops-test-only"
export OWNER_AUTH_TOKEN="phase09-ops-test-owner"
export PROVIDER_MODE="FIXTURE"
export ALLOW_SYNTHETIC_FIXTURES="1"
export MODEL_API_KEY=""
export RUN_META_LIVE_TESTS="0"
export QUEUE_NAME="counterforge-ops-rehearsal"
export WEB_ORIGIN="http://localhost:53001"
export OPS_POSTGRES_PORT="${OPS_POSTGRES_PORT:-55433}"
export OPS_REDIS_PORT="${OPS_REDIS_PORT:-56380}"
export OPS_API_PORT="${OPS_API_PORT:-58001}"
export OPS_WEB_PORT="${OPS_WEB_PORT:-53001}"

COMPOSE=(docker compose --env-file .env.example -p "$PROJECT" -f compose.yaml -f compose.rehearsal.yaml)
PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"

TASK_TMP="$(mktemp -d /tmp/counterforge-ops.XXXXXX)"
chmod 700 "$TASK_TMP"
printf 'Authorization: Bearer %s\n' "$OWNER_AUTH_TOKEN" > "$TASK_TMP/owner-header"
chmod 600 "$TASK_TMP/owner-header"

cleanup() {
  rm -rf -- "$TASK_TMP"
  if [ "${KEEP:-0}" = "1" ] || [ "${1:-}" = "--keep" ]; then
    echo "KEEP=1: rehearsal stack left running (project $PROJECT)"
    return
  fi
  # Verify identity before destroying anything: every container must carry
  # this rehearsal project's label, otherwise refuse to clean up.
  local ids
  ids="$("${COMPOSE[@]}" ps -q 2>/dev/null || true)"
  local id label
  for id in $ids; do
    label="$(docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "$id")"
    if [ "$label" != "$PROJECT" ]; then
      echo "Refusing cleanup: container $id has project label '$label'" >&2
      return 1
    fi
  done
  "${COMPOSE[@]}" down -v --remove-orphans
}
trap 'cleanup' EXIT

echo "== compose config (explicit .env.example, test-only overrides) =="
"${COMPOSE[@]}" config --quiet

echo "== clean volumes: start postgres + redis =="
"${COMPOSE[@]}" up -d postgres redis
for _ in $(seq 1 30); do
  "${COMPOSE[@]}" exec -T postgres pg_isready -U counterforge >/dev/null 2>&1 && break
  sleep 2
done
for db in counterforge_ops_test counterforge_ops_restored; do
  exists="$("${COMPOSE[@]}" exec -T postgres psql -U counterforge -tAc "SELECT 1 FROM pg_database WHERE datname='$db'")"
  if [ "$exists" != "1" ]; then
    "${COMPOSE[@]}" exec -T postgres createdb -U counterforge "$db"
  fi
done

echo "== build + start migrate/api/worker/web =="
"${COMPOSE[@]}" up -d --build
for _ in $(seq 1 60); do
  curl -fsS "http://127.0.0.1:${OPS_API_PORT}/api/v1/health/live" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS "http://127.0.0.1:${OPS_API_PORT}/api/v1/health/live"
echo
curl -fsS -H "@$TASK_TMP/owner-header" \
  "http://127.0.0.1:${OPS_API_PORT}/api/v1/health/ready"
echo

echo "== restart persistence in the app stack (project, blob, counts) =="
PROJECT_JSON="$(curl -fsS -X POST -H "@$TASK_TMP/owner-header" -H 'Content-Type: application/json' -H 'Idempotency-Key: ops-rehearsal-project' -d '{"name":"Ops rehearsal persistence"}' "http://127.0.0.1:${OPS_API_PORT}/api/v1/projects")"
PROJECT_ID="$(printf '%s' "$PROJECT_JSON" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
"${COMPOSE[@]}" exec -T -e PROJECT_ID="$PROJECT_ID" api python -c "
import os
from pathlib import Path
from uuid import UUID
from services.api.app.artifacts import ArtifactStore
from services.api.app.config import Settings
from services.api.app.db import create_database_engine, create_uow_factory
# Registered fixture asset: a real artifact_assets row (not an orphan blob),
# so the artifact_assets count below and the backup manifest cover it.
settings = Settings.from_env()
engine = create_database_engine(settings)
try:
    with create_uow_factory(engine)() as uow:
        ref = ArtifactStore(uow, Path('/data/artifacts')).put(UUID(os.environ['PROJECT_ID']), b'ops-rehearsal-evidence', 'OTHER', 'application/octet-stream')
        uow.commit()
    print(ref.sha256)
finally:
    engine.dispose()
" > "$TASK_TMP/blob-sha.txt"
BLOB_SHA_BEFORE="$(cat "$TASK_TMP/blob-sha.txt")"
COUNTS_SQL="SELECT (SELECT count(*) FROM projects) || '/' || (SELECT count(*) FROM artifact_assets) || '/' || (SELECT count(*) FROM audit_events)"
COUNTS_BEFORE="$("${COMPOSE[@]}" exec -T postgres psql -U counterforge -d counterforge -tAc "$COUNTS_SQL" | tr -d '[:space:]')"
"${COMPOSE[@]}" restart api worker
for _ in $(seq 1 60); do
  curl -fsS -H "@$TASK_TMP/owner-header" "http://127.0.0.1:${OPS_API_PORT}/api/v1/health/ready" >/dev/null 2>&1 && break
  sleep 2
done
curl -fsS -H "@$TASK_TMP/owner-header" "http://127.0.0.1:${OPS_API_PORT}/api/v1/projects/$PROJECT_ID" >/dev/null
COUNTS_AFTER="$("${COMPOSE[@]}" exec -T postgres psql -U counterforge -d counterforge -tAc "$COUNTS_SQL" | tr -d '[:space:]')"
BLOB_SHA_AFTER="$("${COMPOSE[@]}" exec -T -e PROJECT_ID="$PROJECT_ID" -e EXPECTED="$BLOB_SHA_BEFORE" api python -c "
import hashlib, os
from pathlib import Path
from uuid import UUID
from services.api.app.artifacts import BlobRef, BlobStore
store = BlobStore(Path('/data/artifacts'))
# Re-derive the content-addressed key from the recorded hash.
key = store.key(UUID(os.environ['PROJECT_ID']), os.environ['EXPECTED'], 'application/octet-stream')
data = store.read_verified(BlobRef(UUID(os.environ['PROJECT_ID']), key, os.environ['EXPECTED'], 22, 'application/octet-stream'))
print(hashlib.sha256(data).hexdigest())
" | tr -d '[:space:]')"
[ "$COUNTS_BEFORE" = "$COUNTS_AFTER" ] || { echo "Restart counts differ: $COUNTS_BEFORE vs $COUNTS_AFTER" >&2; exit 1; }
[ "$BLOB_SHA_BEFORE" = "$BLOB_SHA_AFTER" ] || { echo "Restart blob hash differs" >&2; exit 1; }
echo "restart persistence: counts $COUNTS_AFTER, blob hash verified"

echo "== task-local pg_dump/pg_restore wrappers (docker exec, no secrets in argv) =="
WRAPPER_DIR="$TASK_TMP/wrappers"
mkdir -m 700 "$WRAPPER_DIR"
PG_CONTAINER="$("${COMPOSE[@]}" ps -q postgres)"
cat > "$WRAPPER_DIR/pg_dump" <<EOF
#!/usr/bin/env bash
set -euo pipefail
out=""; args=()
for a in "\$@"; do
  case "\$a" in
    --file=*) out="\${a#--file=}" ;;
    --host=*|--port=*) ;;
    *) args+=("\$a") ;;
  esac
done
docker exec -i "$PG_CONTAINER" pg_dump "\${args[@]}" > "\$out"
EOF
cat > "$WRAPPER_DIR/pg_restore" <<EOF
#!/usr/bin/env bash
set -euo pipefail
args=(); dump=""
for a in "\$@"; do
  case "\$a" in
    --host=*|--port=*) ;;
    -*) args+=("\$a") ;;
    *) dump="\$a" ;;
  esac
done
trap 'docker exec -i "$PG_CONTAINER" rm -f /tmp/ops-restore.dump' EXIT
docker cp "\$dump" "$PG_CONTAINER:/tmp/ops-restore.dump"
docker exec -i "$PG_CONTAINER" pg_restore "\${args[@]}" /tmp/ops-restore.dump
docker exec -i "$PG_CONTAINER" rm -f /tmp/ops-restore.dump
EOF
chmod 700 "$WRAPPER_DIR/pg_dump" "$WRAPPER_DIR/pg_restore"
export PATH="$WRAPPER_DIR:$PATH"
export OPS_PG_DUMP="$WRAPPER_DIR/pg_dump"
export OPS_PG_RESTORE="$WRAPPER_DIR/pg_restore"

echo "== gated rehearsal tests (fixture persistence, Redis recovery, backup/restore) =="
export RUN_COUNTERFORGE_OPS_REHEARSAL=1
export COUNTERFORGE_TEST_DATABASE_URL="postgresql+psycopg://counterforge:${POSTGRES_PASSWORD}@127.0.0.1:${OPS_POSTGRES_PORT}/counterforge_ops_test"
export COUNTERFORGE_OPS_RESTORED_DATABASE_URL="postgresql+psycopg://counterforge:${POSTGRES_PASSWORD}@127.0.0.1:${OPS_POSTGRES_PORT}/counterforge_ops_restored"
export COUNTERFORGE_TEST_REDIS_URL="redis://127.0.0.1:${OPS_REDIS_PORT}/15"
"$PY" -m pytest tests/integrations/test_ops_rehearsal.py -q

echo "== restart services; persisted work and heartbeat must return =="
"${COMPOSE[@]}" restart api worker
for _ in $(seq 1 60); do
  if curl -fsS -H "@$TASK_TMP/owner-header" \
      "http://127.0.0.1:${OPS_API_PORT}/api/v1/health/ready" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -fsS -H "@$TASK_TMP/owner-header" \
  "http://127.0.0.1:${OPS_API_PORT}/api/v1/health/ready"
echo
curl -fsS -H "@$TASK_TMP/owner-header" \
  "http://127.0.0.1:${OPS_API_PORT}/api/v1/projects" >/dev/null
echo "restart check: owner project list reachable after restart"

echo "== read-only latency sample (observed only, no model calls) =="
if ! OWNER_AUTH_TOKEN="$OWNER_AUTH_TOKEN" "$PY" -m scripts.measure_latency \
  --base-url "http://127.0.0.1:${OPS_API_PORT}" --samples 20; then
  echo "Latency measurement did not complete; rehearsal FAILED" >&2
  exit 1
fi

echo "REHEARSAL PASS (project $PROJECT)"
