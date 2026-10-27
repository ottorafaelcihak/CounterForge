# Phase 01 implementation handoff

**Scope:** [plan-01.md](plan-01.md). **Verification date:** October 28, 2026 (Europe/Madrid).

## Delivered boundaries

- uv/Python 3.12 workspace with six installable packages, typed FastAPI API and disabled worker boundary.
- Strict VTC/plan/state contracts, normalized provider protocols/results and allowlisted public DTOs.
- SHA-keyed LOCAL_FIXTURE target with an explicit expected count and strict integer/JSON parsing.
- Typed v1 route surface with honest 501 business stubs, sanitized validation/errors and non-ready dependency status.
- Deterministic JSON Schema/OpenAPI and generated TypeScript, drift checking, npm lockfile and CI gates.
- Minimal Next.js/React/Tailwind shell; setup commands are in the [root README](../../README.md).

## Decisions and limits

- Effective VTC counts are 1–16, with one distinct UUID mask reference per instance. Canonical contract drafts require mask review. Actual seed ownership and approval verification remain database service checks for phase 02.
- Proposal text bounds are explicit engineering defaults: hypothesis 1–1000 characters, instruction 1–2000, finite nonnegative advisory cost. Server pricing is still authoritative in future execution.
- Generative/background-family plans require `background_only`; deterministic brightness may use `full_image`. This schema does not establish image validity.
- Fixtures receive expected-count context explicitly; prompts are never used to infer ground truth. Real provider adapters and confirmation logic are not implemented here.
- The public API never uses internal ProviderResult/TargetResult objects as response DTOs. Internal provider schema exports are labeled separately.
- Phase 01 declares the core and reserved list/read/job/mask-revision routes as typed 501 stubs, so documented links have a declared route. Phase 02 owns behavior and final DTO integration for those additions.
- Webpack and TypeScript's compiler API are selected for reproducible local builds: this environment blocks Turbopack's child-process worker port. Both dev/build scripts use the same bundler. TypeScript 5.9.3 satisfies openapi-typescript's peer contract.
- ESLint 9.39.5 satisfies the current Next.js import/react/accessibility plugin peer ranges; npm reports that ESLint major as deprecated. ESLint 10 was checked but its plugin peer ranges conflict. No application vulnerability was reported by npm's installation audit; reassess the lint toolchain when upstream plugins support the next major.
- HTTP tests use in-process async ASGI transport, avoiding the environment's blocked cross-thread portal behavior. Default pytest also blocks outbound sockets.

## Verification record

The coordinator ran checks in the final workspace. A clean temporary copy under
`/tmp/counterforge-phase01-clean` also installed all seven Python distributions
from `uv.lock` and ran `npm ci` from its npm lockfile without credentials.

| Check | Result |
|---|---|
| `uv sync --locked --offline` | Passed in final workspace and clean copy using the dependency cache |
| `npm ci --prefix apps/web` | Passed in clean copy; 389 audited packages, zero reported vulnerabilities |
| `make generate-contracts` + `make check-contracts` | Passed; repeated export byte-identical |
| `make lint` | Ruff checks/format and ESLint passed |
| `make typecheck` | mypy: 31 source files; TypeScript passed |
| `make test` / full `pytest` | 85 passed: 16 unit and 69 contract cases |
| Independent source-derived contract script | 22 additional cases passed |
| `make build-web` | Next.js production build passed with static `/` and not-found pages |
| Documentation links | README/docs relative links resolved |

The installed uv executable for this session was bootstrapped in
`/tmp/counterforge-tools`; commands used that directory on PATH and a temporary uv
cache. Standard development setup requires uv on PATH as documented in the README.
Make's Python commands use locked offline execution after the initial sync.

Resolved core versions: Python 3.12.3, uv 0.13.6, Node 24.18.0, npm 11.16.0,
FastAPI 0.162.0, Pydantic 2.14.1, Pytest 9.2.0, Next.js 16.4.3, React 19.3.1,
Tailwind 4.3.3, TypeScript 5.9.3, openapi-typescript 7.13.0 and ESLint 9.39.5.
The lockfiles contain the complete transitive version and integrity records.

Tests were written before the initial modules (missing-module RED), then passed
against the implemented contracts/fixtures. Two later behavioral RED→GREEN checks
covered deterministic full-image brightness and the reserved read-route stubs.
The default suite includes an attempted outbound socket with inherited live-test
opt-in and verifies that it is blocked.

Muse's final review was source inspection; shell execution was disabled in that
reviewer session, so the coordinator verified every executable check above.
The missing reserved-route declarations were fixed and tested. The proposed
strict-float issue was checked and rejected: installed Pydantic 2.14.1 accepts JSON
`10` and `10.0` as finite numbers while rejecting booleans, strings, NaN and
Infinity; a full browser-style numeric round-trip test now pins that behavior.
The zero-count documentation note is in the root README and this handoff.

**Deferred minor:** The drift checker compares the current schema/TypeScript
artifact bytes before/after generation, but does not reject an unrelated stale
extra JSON file that the generator no longer owns. There are eight declared schema
outputs in this phase; an explicit generated-file inventory can be added when
schema removal is introduced.

## Team participation

- Coordinator: this Codex session, implementation and integration.
- Muse bridge: preflight acceptance audit (`muse-wonder-1.0`) and independent final source review using its configured model.
- Both requested Codex bridge roles were attempted, but their configured `gpt-6.1-sol` model was rejected by the CLI account before editing.
- Claude fallback was attempted, then cancelled after no progress was visible. Bridge cancellation left an orphan child temporarily active; it was identified by its exact process group and terminated. Its conflicting draft tests were retained outside the repository and reconciled to the canonical interfaces. No unrelated desktop/agent process was stopped.

## Next phase

[Phase 02](plan-02.md) adds PostgreSQL/Alembic, durable jobs, authenticated CRUD,
artifact storage and accounting. Its implementer must preserve the canonical
types and replace stubs only as their actual behavior and failure paths pass.
No real-provider smoke, database operation or model-failure discovery is claimed
by Phase 01.
