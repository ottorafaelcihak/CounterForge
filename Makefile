UV ?= uv
RUN := $(UV) run --locked --offline
PYTHON := $(RUN) python

.PHONY: lint typecheck test test-unit test-contract test-db migrate services-up services-down test-e2e compose-check generate-contracts check-contracts dev-api dev-web build-web

lint:
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	npm run lint --prefix apps/web

typecheck:
	$(RUN) mypy
	npm run typecheck --prefix apps/web

test: test-unit test-contract

test-unit:
	$(RUN) pytest tests/unit

test-contract:
	$(RUN) pytest tests/contracts

test-db:
	@test -n "$$COUNTERFORGE_TEST_DATABASE_URL" || { echo "COUNTERFORGE_TEST_DATABASE_URL is required; use a disposable loopback PostgreSQL database." >&2; exit 2; }
	$(RUN) pytest tests/integrations --allow-hosts=127.0.0.1,::1,localhost

migrate:
	$(PYTHON) -m services.api.app.db.migrations

services-up:
	docker compose up -d --build

services-down:
	docker compose down

compose-check:
	docker compose config --quiet

generate-contracts:
	$(PYTHON) -m scripts.generate_contracts
	npm run generate-types --prefix apps/web

check-contracts:
	$(PYTHON) -m scripts.check_contracts

dev-api:
	$(RUN) uvicorn services.api.app.main:app --host 127.0.0.1 --port 8000

dev-web:
	npm run dev --prefix apps/web

build-web:
	npm run build --prefix apps/web

.PHONY: smoke-meta recover-jobs
smoke-meta:
	$(PYTHON) -m scripts.smoke_meta

recover-jobs:
	$(PYTHON) -m services.worker.app.main --recover-only

test-e2e:
	@test -n "$$COUNTERFORGE_TEST_DATABASE_URL" && test -n "$$COUNTERFORGE_TEST_REDIS_URL" || { echo "Explicit disposable PostgreSQL and loopback Redis /15 URLs are required." >&2; exit 2; }
	RUN_COUNTERFORGE_E2E=1 $(RUN) pytest tests/e2e

.PHONY: verify-export
verify-export:
	$(PYTHON) -m scripts.verify_export

# Operational tooling. All targets are NO MONEY: no provider calls.
# BACKUP_DIR / BACKUP_ROOT / ARTIFACT_DIR / LATENCY_SAMPLES are explicit.
.PHONY: ops-backup ops-restore ops-verify-backup ops-prune-backups ops-latency ops-rehearsal ops-retention
BACKUP_DIR ?= ./backups
ops-backup:
	@test -n "$$PGHOST" -o -n "$$DATABASE_URL" || { echo "Explicit PG components or DATABASE_URL are required." >&2; exit 2; }
	$(PYTHON) -m scripts.backup_restore backup --output-dir $(BACKUP_DIR)

# Restore is non-destructive and refuses nonempty destinations; it requires
# an explicitly disposable target database in the environment.
ops-restore:
	@test "$(CONFIRM_DISPOSABLE)" = "1" || { echo "CONFIRM_DISPOSABLE=1 is required for an empty disposable target." >&2; exit 2; }
	@test -n "$(BACKUP_PATH)" || { echo "BACKUP_PATH=<backup dir> is required." >&2; exit 2; }
	@test -n "$(ARTIFACT_DIR)" || { echo "ARTIFACT_DIR=<empty dir> is required." >&2; exit 2; }
	$(PYTHON) -m scripts.backup_restore restore --backup-dir $(BACKUP_PATH) --artifact-root $(ARTIFACT_DIR) --confirm-disposable-restore

ops-verify-backup:
	@test -n "$(BACKUP_PATH)" || { echo "BACKUP_PATH=<backup dir> is required." >&2; exit 2; }
	$(PYTHON) -m scripts.backup_restore verify --backup-dir $(BACKUP_PATH)

# Dry-run by default; pass DELETE=1 only after reviewing the listed backups.
ops-prune-backups:
	$(PYTHON) -m scripts.backup_restore prune-backups --backup-root $(BACKUP_DIR) --retention-days $(or $(RETENTION_DAYS),30) $(if $(DELETE),--delete,)

# Referenced evidence is never swept; this remains orphan-only and dry-run
# unless DELETE=1 is passed explicitly (see scripts/maintain_artifacts.py).
ops-retention:
	$(PYTHON) -m scripts.maintain_artifacts --older-than-hours $(or $(RETENTION_HOURS),720) $(if $(DELETE),--delete,--dry-run)

LATENCY_SAMPLES ?= 50
ops-latency:
	$(PYTHON) -m scripts.measure_latency --samples $(LATENCY_SAMPLES)

# Disposable Compose rehearsal; root-run, owns only its named project.
ops-rehearsal:
	bash scripts/rehearse_ops.sh

# Explicit NO MONEY default: this exercises ledger/report methodology only.
# Pass EVALUATE_ARGS to route another CLI command, e.g.
#   make evaluate EVALUATE_ARGS="verify-bundle /path/to/bundle"
.PHONY: evaluate
EVALUATE_PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(PYTHON))
evaluate:
	$(EVALUATE_PYTHON) -m evaluations.cli $(if $(EVALUATE_ARGS),$(EVALUATE_ARGS),fixture)

# Offline Phase 10 structural validation only; not release readiness.
# No network, provider, database, or external-link/scanner verification.
.PHONY: release-check release-inventory
release-check:
	$(EVALUATE_PYTHON) -m scripts.release_checks

release-inventory:
	$(EVALUATE_PYTHON) -m scripts.release_checks --inventory
