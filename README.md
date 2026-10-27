# CounterForge

Find the failure. Prove the weakness. Prevent the regression.

CounterForge is a visual-model reliability laboratory: controlled image changes,
independent ground-truth checks, repeated evaluation and portable regression tests.
The initial task is single-image object counting.

Phase 01 establishes shared contracts, offline fixtures and development tooling.
Persistence, live Meta integrations and experiment execution are planned in later
phases. Business API routes currently return a sanitized `501 NOT_IMPLEMENTED`;
they do not create projects, run paid calls or simulate successful experiments.

## Development setup

Requires Python 3.12, uv 0.13.6 or compatible, Node.js 24 LTS and npm. No API keys,
database or Redis are needed for Phase 01. Keep local secrets in an ignored `.env`
only when a later phase implements secret loading; this scaffold does not load it.

```bash
uv sync --locked
npm ci --prefix apps/web
make check-contracts
make lint
make typecheck
make test
make build-web
```

`make test` runs unit and contract checks with outbound network sockets disabled,
even if `RUN_META_LIVE_TESTS=1` is inherited. It never calls billable endpoints.
`make check-contracts` regenerates artifacts and fails on drift; after intentionally
changing canonical contracts, run `make generate-contracts` and review the changes.

```bash
make dev-api   # http://127.0.0.1:8000/docs and /openapi.json
make dev-web   # http://127.0.0.1:3000
```

Liveness is `/api/v1/health/live`; readiness deliberately returns 503 until the
database and queue exist. Other typed v1 routes document future request/response
contracts and consistently return 501 after input validation. Owner authentication
and public-demo authorization are phase 02/09 work, before business routes enable.

## Contracts and fixtures

- `counterforge_contracts` owns VTC `1.0.0`, plans, states, normalized provider results and safe API DTOs.
- Pydantic produces JSON Schema and FastAPI OpenAPI; openapi-typescript generates the browser client types.
- Object counting requires 1–16 protected instances with distinct UUID mask references. Zero-object cases require a future contract extension. Database mask ownership/review checks come in phase 02.
- `LOCAL_FIXTURE` is an offline target keyed by image SHA-256, with an explicit expected count. Unknown hashes and ambiguous replies stay UNKNOWN. Fixture results are never evidence of a real AI weakness.
- Meta, mutation, verification, target and regression packages expose shared boundaries; live adapters, image processing and export execution arrive in their owning phases.

## Project structure

```text
CounterForge/
├── apps/web/             # Next.js shell and generated API types
├── services/api/         # Typed FastAPI boundary
├── services/worker/      # Explicitly disabled scheduler boundary
├── packages/             # Six installable Python workspace packages
├── scripts/              # Deterministic schema export and drift check
├── tests/                # No-network unit and contract checks
└── docs/plans/           # Ordered implementation phases and evidence
```

## Documentation

Start with the [documentation index](docs/README.md), [implementation roadmap](docs/plans/README.md)
and [Phase 01 handoff](docs/plans/phase-01-handoff.md). The PRD, technical specification
and proposed SQL remain the source documents; the SQL has not been applied.
