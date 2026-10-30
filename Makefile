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

# Explicit NO MONEY default: this exercises ledger/report methodology only.
# Pass EVALUATE_ARGS to route another CLI command, e.g.
#   make evaluate EVALUATE_ARGS="verify-bundle /path/to/bundle"
.PHONY: evaluate
EVALUATE_PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,$(PYTHON))
evaluate:
	$(EVALUATE_PYTHON) -m evaluations.cli $(if $(EVALUATE_ARGS),$(EVALUATE_ARGS),fixture)
