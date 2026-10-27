# Phase 01 — Foundation and Shared Contracts Implementation Plan

> **For agentic workers:** Implement tasks in order using `superpowers:executing-plans`, or use `superpowers:subagent-driven-development` when delegation is explicitly selected. Track work with the checkboxes and read the linked specifications before editing.

**Goal:** Establish a reproducible workspace and a single source of truth for all application contracts.

**Architecture:** One repository contains a Next.js web app, FastAPI service, RQ worker and focused Python packages. Pydantic owns application schemas; FastAPI OpenAPI generates TypeScript client types. Fixtures define adapter boundaries before live integration.

**Tech stack:** Python 3.12+, uv, Pydantic, FastAPI, Node.js 20.17+ or supported LTS, npm, Next.js/React/Tailwind, Pytest, Ruff, mypy, TypeScript, ESLint and openapi-typescript.

**Spec:** [Technical specification](../CounterForge_Technical_Specification_v1.0.md) §§0–6, 8, 9.1, 13.1, 17–19; [PRD](../CounterForge_PRD_v1.0.md) §§6–10. Supports FR-03, FR-10 and FR-14.

**Prerequisite:** Source documents only. **Next:** [Phase 02](plan-02.md). **Target:** October 27, 2026.

## Global constraints

- Canonical VTC version is `1.0.0`; task type is `object_counting`.
- Use `VALID / INVALID / REVIEW_REQUIRED` for candidate validity and explicit unknown/error target outcomes.
- Choose uv, npm and openapi-typescript; commit lockfiles with compatible pinned dependencies.
- P0 has no arbitrary target URLs, multi-tenant billing, video or model repair.
- No paid provider access in default tests. Keep `.env` and attachment staging out of version control; do not inspect or copy existing credentials.

## Review focus

- Contradictory count/mask/reproduction fields fail before an experiment exists.
- Unknown and malformed model output never becomes an incorrect-count finding.
- Python, JSON Schema and TypeScript use the same version and enum values.
- Provider response objects cannot leak into public DTOs.
- A clean checkout installs deterministically without live credentials.

## Tasks

### Task 1: Reproducible workspace and fixture-only checks

**Files:** Root `pyproject.toml`, `uv.lock`, `Makefile`, `.gitignore`, `.env.example`; `apps/web/package.json` and npm lockfile; package/service skeletons from technical specification §3; `.github/workflows/ci.yml`.

**Interfaces:** Produces importable `counterforge_contracts`, `counterforge_meta`, `counterforge_mutations`, `counterforge_verification`, `counterforge_targets` and `counterforge_regression` packages. Services consume these packages rather than relative path hacks.

- [x] Establish the specified directories with focused modules; select compatible framework versions and record them in lockfiles. Use Python 3.12 as the initial pinned interpreter and a supported Node LTS satisfying the source floor.
- [x] Add safe placeholder settings, including `RUN_META_LIVE_TESTS=0`, default experiment limits and server-only secret names. Ignore `.env`, local artifacts, dependency caches and `.codex-attachments/`.
- [x] Add fixture-only CI jobs and Make targets for lint, typecheck, unit and contract tests. Define `make test` as the no-network unit/contract aggregate; later phases add their DB/browser/export targets without putting paid calls into this aggregate. CI supplies no provider credentials.
- [x] Verify `uv sync --locked` and `npm ci --prefix apps/web`; both must install from lockfiles. Run `make lint` and `make typecheck`; both exit zero.

### Task 2: Canonical VTC, plan and state contracts

**Files:** `packages/contracts/counterforge_contracts/{vtc,plans,versions}.py`, schema outputs and `tests/contracts/test_vtc.py`.

**Interfaces:** Produces `VisualTestContract`, `MutationPlan`, `ExperimentState`, `MutationState`, `ValidityVerdict` and target-outcome enums. `VisualTestContract.model_json_schema()` generates the canonical VTC JSON Schema.

- [x] Write tests for technical specification §5's exact bounds, forbidden extra fields, wrong schema version and unsupported mutation family. Require `expected.value == protected.expected_instances` and `required_failures <= attempts`.
- [x] Implement the normative models. For this positive-instance counting MVP the combined constraints allow 1–16 protected instances; document that zero-object cases need a later contract extension even though `ExpectedCount` alone permits zero.
- [x] Use UUID values for persisted mask asset references. Pure schema validation checks shape; phase 02's service checks seed ownership and reviewed state before contract creation/start.
- [x] Define the exact state enums in §6, including requested pause/cancel states and `SEED_INVALID_FOR_SEARCH`. Validate plan strength in `[0,1]`; background-only generative plans cannot repaint protected objects.
- [x] Verify `make test-contract`: mismatched count, too many objects, invalid limits, missing reviewer, unsupported enum and reproduction constraints all reject with explicit errors.

### Task 3: Provider, target and application DTO boundaries

**Files:** Contract response/event modules, `packages/targets/counterforge_targets/{protocol,fixture}.py`, adapter protocol modules and `tests/contracts/test_adapter_contracts.py`.

**Interfaces:** Keep the four async method inputs from technical specification §9.1 and use its later normalized result contracts rather than the introductory `dict` placeholders: `ExperimentPlanner.plan(contract, history, remaining_budget) -> list[MutationPlan]`, `Segmenter.segment(image_bytes, concept) -> SegmentationResult`, `SceneGenerator.create_scene(source_bytes, instruction) -> bytes` and `TargetModel.evaluate(image_bytes, prompt, pinned) -> TargetResult`. Retain `ProviderResult` metadata alongside persisted operation results; segmentation and target fields follow §§9.3/9.5.

- [x] Add contract tests with sanitized fake planner, segmenter, scene generator and target results; make unknown/refused/incomplete results explicit.
- [x] Define DTOs for §8's requests/responses, the nested error envelope and sanitized event payloads. `JobRef` has `job_id`, state and a status link; `ExperimentRef` retains `experiment_id`, state and links.
- [x] Reserve minimal additive API routes needed to complete the specified UI: project/seed/target lists, mask reads/revisions, contract reads, job status, finding detail and regression listing. Freeze their DTOs in phase 02 instead of having the frontend invent responses.
- [x] Implement `LOCAL_FIXTURE` responses keyed by known fixture image hashes and store an unmistakable fixture provider identity.
- [x] Verify fixtures with `make test-contract`; test that browser DTOs exclude secret references, internal storage keys, provider headers and signed provider URLs.

### Task 4: Schema and client-type generation

**Files:** Contract schema generation scripts, `services/api/app/main.py`, web generated types and CI schema-drift checks.

**Interfaces:** FastAPI exposes `/openapi.json`; generated TypeScript represents API v1. Provider-level structured output uses the small plan schema, not the complete product API schema.

- [x] Add `make generate-contracts` to export deterministic JSON Schema/OpenAPI and run openapi-typescript. Register the typed API surface without pretending unimplemented business operations work.
- [x] Add CI comparison of generated files against committed artifacts; unexpected drift fails.
- [x] Verify `make generate-contracts`, `make test-contract` and `make typecheck`; a second generation produces no content changes.
- [x] Commit this phase's scoped changes after the verification gates pass, with `chore: establish CounterForge contracts and tooling`.

## Exit criteria and evidence

- [x] Fresh installs, lint, typechecking, schema generation and fixture-only tests pass without credentials.
- [x] All shared enums and protocol results have one canonical owner; later packages import them.
- [x] Save dependency versions, test output and contract-generation evidence in the implementation handoff.

## Handoff

Phase 02 consumes the schemas, state enums, DTOs and fixture protocols. No storage,
provider availability, live results or paid-call behavior is claimed by this phase.
