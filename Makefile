UV ?= uv
RUN := $(UV) run --locked --offline
PYTHON := $(RUN) python

.PHONY: lint typecheck test test-unit test-contract test-db migrate services-up services-down compose-check generate-contracts check-contracts dev-api dev-web build-web

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
	$(RUN) pytest tests/integrations

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
