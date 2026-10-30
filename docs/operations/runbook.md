# Operations runbook

Health, backup, offline verification, retention, latency and the fixture rehearsal
make no provider calls. Recovery re-enqueues authorized work and can resume billing
under LIVE workers. Stop workers or disable providers before restore/recovery and
keep them stopped until retained jobs, budgets and unknown charges are reconciled.
Provider smoke
(`make smoke-meta`) stays a separate, explicitly gated opt-in and is never
part of health checks, startup, restart, backup, or restore.

## Health and readiness

- Liveness: `curl http://127.0.0.1:8000/api/v1/health/live`
- Readiness (owner-only, booleans only):
  use a protected temporary header file to keep the token out of process arguments:

  ```bash
  ( umask 077
    header_dir=$(mktemp -d)
    trap 'rm -rf -- "$header_dir"' EXIT
    printf 'Authorization: Bearer %s\n' "$OWNER_AUTH_TOKEN" > "$header_dir/owner-header"
    curl -H "@$header_dir/owner-header" http://127.0.0.1:8000/api/v1/health/ready
  )
  ```

  Readiness
  reports `database`, `migrations`, `redis`, `artifacts`, and
  `worker_heartbeat` separately. A stale worker heartbeat (15 s TTL)
  means the worker is down or stuck, even if Redis itself answers.

## Backup

Supported source: a dedicated CounterForge database with every current application
and Alembic table in `public`, and no other user tables/views in any schema.
Empty application tables are valid. Non-public, mixed and incomplete schemas
are rejected before dumping. DSN query parameters (including SSL/options) are
explicitly unsupported and rejected, never silently dropped; use another
verified backup procedure when those connection settings are required.

Prerequisites: explicit PG environment (`PGHOST/PGPORT/PGDATABASE/PGUSER/
PGPASSWORD` or a fully expanded `DATABASE_URL`), `ARTIFACT_ROOT`, and
pg_dump 16 on PATH (installed in the API/worker images; otherwise pass
`--pg-dump` with a task-local wrapper — the CLI fails safely if missing).

```bash
make ops-backup BACKUP_DIR=./backups
# or: .venv/bin/python -m scripts.backup_restore backup --output-dir ./backups
```

For a fully consistent snapshot, pause the worker first (maintenance
window); pg_dump is internally consistent, and artifacts are immutable,
but queued work keeps moving otherwise. Each backup directory contains
`database.dump`, referenced `artifacts/…`, `snapshot.json` (table counts,
active/pending jobs, pending export intent, all canonical table counts), and `manifest.json` with
SHA-256 for every unique blob/file (artifact aliases retain every DB row) (0600 files, atomic rename into place).

Verify offline at any time: `make ops-verify-backup BACKUP_PATH=<dir>`.

Verification and restore require the application revision matching the backup's
canonical table inventory. A later migration that adds tables can make an earlier
manifest fail the current inventory check; restore with the matching revision,
then migrate the restored database before starting writers.

## Restore (disposable destinations only)

Restore never overwrites production. It requires
`--confirm-disposable-restore`, an empty/nonexistent artifact directory,
and a target database with no relations in any user schema, and it verifies the whole
backup before changing anything.

```bash
createdb counterforge_restored            # empty, disposable
# point PGDATABASE (or DATABASE_URL) at counterforge_restored, then:
make ops-restore CONFIRM_DISPOSABLE=1 BACKUP_PATH=<backup dir> ARTIFACT_DIR=<empty dir>
```

After restore it re-checks every canonical table count against the manifest,
unique blob counts separately from artifact row counts, and every artifact hash.
The database must be disposable and all writers/workers stopped throughout; the
Make target refuses to run without the explicit confirmation above. Verify a restored regression bundle with
`make verify-export` semantics via `counterforge_regression.bundle.verify_bundle`
on the restored ZIP (the rehearsal does exactly this).

## Redis loss recovery

Do not flush shared Redis. Recovery reads durable PostgreSQL intent:

```bash
make recover-jobs   # python -m services.worker.app.main --recover-only
```

Keep workers stopped or providers disabled until reconciliation.
Queued jobs/experiments are re-enqueued and may resume billable authorized
work when LIVE workers run. Dispatched work with an expired
lease is **not** retried: it becomes `REVIEW_REQUIRED` with
`UNKNOWN_CHARGE` for a human decision.

## Retention (30 days, configurable, dry-run default)

- Backups: `make ops-prune-backups` lists backups older than
  `RETENTION_DAYS` (default 30); add `DELETE=1` only after review.
  Unverifiable directories are never deleted.
- Artifacts: `make ops-retention` is orphan-only
  (`scripts/maintain_artifacts.py`, default 720 h). Referenced or active
  evidence is never swept, regardless of age.

## Latency (observed, read-only)

```bash
make ops-latency LATENCY_SAMPLES=50
```

Reports observed p50/p95 against the specification's 500 ms target for
this machine and run only. It is not a benchmark guarantee, and it makes
no model calls.

## Disposable rehearsal (root-run)

```bash
make ops-rehearsal        # or: bash scripts/rehearse_ops.sh [--keep]
```

This builds a fresh task-owned `counterforge-phase09-ops-<timestamp>-<pid>`
Compose project per run (a rerun never owns or deletes a prior `--keep` stack)
(`--env-file .env.example`, fixture mode, empty model key, distinct
loopback ports 55433/56380/58001/53001), seeds a fixture run, resets only
its Redis DB /15, restarts services, runs backup/restore into an empty
database and artifact dir, verifies hashes/counts and a known bundle,
samples latency, then removes only its own labeled project volumes.
Cleanup refuses to proceed if any container lacks the project label.

## Limitations

- Backup covers PostgreSQL plus DB-referenced artifacts only; Redis is
  intentionally excluded (recoverable from PostgreSQL).
- pg_dump/pg_restore major versions must match the PostgreSQL 16 server.
- Latency figures are single-machine observations, not guarantees.
- A real public demo snapshot still requires explicit Phase 10 approval.
- Keep `OWNER_LABEL` stable for retained projects and snapshots; changing it
  changes owner scope and makes their public snapshots fail closed.
