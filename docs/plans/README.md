# CounterForge implementation roadmap

**Status:** Phases 01–06 are implemented with fixture verification. Phase 06 adds
portable regression exports, evidence/library screens and a predeclared small
comparison. Its real runs used 10 calls / USD 0.011852 confirmed, plus up to USD
0.012 unresolved: random screening was correct; Spark paused after an ambiguous
image timeout. No genuine stable failure or live regression export was demonstrated.
See the [Phase 06 handoff](phase-06-handoff.md) and
[comparison report](phase-06-comparison-results.md). Phase 05 separately used
13 calls / USD 0.041512. Usable SAM masks on this illustration, automatic threshold
calibration and held-out evaluation remain unmet. Phases 07–11 remain planned.

Read plans in order. Checked implementation gates refer to their verification
handoffs; fixture success never substitutes for the outstanding live/human gates
or future benchmark measurements.

## Sources and authority

- [PRD v1.0](../CounterForge_PRD_v1.0.md): product goals, FR priorities and user journeys.
- [Technical specification v1.0](../CounterForge_Technical_Specification_v1.0.md): normative application contracts, architecture, algorithms and technical acceptance tests.
- [PostgreSQL schema v1.0](../CounterForge_PostgreSQL_Schema_v1.0.sql): initial database migration baseline; matches the SQL embedded in the technical specification.

Use the technical specification's VTC `1.0.0`, `/api/v1` routes, state names,
`findings` and `regression_exports` terminology where the PRD uses illustrative
alternatives. Refer to acceptance tests as **PRD-AT-xx** or **TS-AT-xx** because
the documents assign different meanings to the same numbers.

## Phases and gates

The dates below are the source documents' target schedule, not duration estimates
or evidence of completion. Failed gates carry forward; a calendar date does not
waive a correctness, budget or provenance requirement.

| Plan | Deliverable | Prerequisite | Exit gate | Source target |
|---|---|---|---|---|
| [01 — Foundation and contracts](plan-01.md) | Tooling, typed contracts, fixture tests and CI | Source documents | Schemas and cross-field tests pass | Oct 27, 2026 |
| [02 — Persistence and API foundation](plan-02.md) | Database, artifact store, owner auth and budget primitives | 01 | Seed/contract round-trip and integrity tests pass | Oct 27 |
| [03 — Meta integrations and masks](plan-03.md) | Provider adapters, seed setup and audited mask review | 02 | Fixture gates pass; enabled live smokes produce usable evidence | Oct 28 |
| [04 — Mutations and verification](plan-04.md) | Controlled image changes and independent validity checks | 03 | VALID, INVALID and REVIEW_REQUIRED examples have evidence | Oct 29 |
| [05 — Bounded experiments](plan-05.md) | Durable baseline/search/confirmation loop and cockpit | 04 | End-to-end fixture run, real search and recovery/budget tests | Oct 30 |
| [06 — Regression export and MVP](plan-06.md) | Portable bundles, finding evidence and small random baseline | 05 | Clean runner passes healthy target and fails broken target | Oct 30 |
| [07 — Adaptive search and refinement](plan-07.md) | Feedback planning, bounded minimization and version retests | 06 | History affects plans; reduced findings remain valid and confirmed | Oct 31–Nov 1 |
| [08 — Evaluation and audit](plan-08.md) | Frozen dataset, matched-budget comparison and measured report | 07 | Transparent denominators, exclusions, audit and spend | Nov 2 |
| [09 — Product completion and operations](plan-09.md) | Complete UI, public gallery and hardened local deployment | 08 | Browser, access and operational gates pass | Nov 3 |
| [10 — Release and submission](plan-10.md) | Reproducible release, attribution, demo and submission evidence | 09 | Acceptance/deviation report and outsider access checks | Nov 4 |
| [11 — Post-MVP roadmap](plan-11.md) | Separate P2 workstreams and design gates | 10 | New work requires its own approved design and plan | After MVP |

Default execution is sequential. Interfaces can be developed against fixtures,
but a phase cannot claim its integration gate before its prerequisites pass.
Essential setup, mask review, status and evidence screens ship in phases 03–06;
phase 09 completes the product experience. Public demo publication is phase 10.

## Shared decisions

- Use Python 3.12+, PostgreSQL 16+, Redis/RQ, Next.js/React/Tailwind, Docker Compose, Pillow/OpenCV/NumPy and Pytest/Playwright as specified. Choose `uv`, npm and `openapi-typescript`; pin compatible versions in phase 01, respecting Node.js 20.17+ or a supported LTS.
- Use the filesystem artifact store first. Keep a single-owner deployment, curated targets and single-image object counting throughout P0/P1.
- Implement the exact supplied SQL through migration `0001`. Keep workflow additions in a reviewed follow-up migration, rather than silently editing the source schema. A durable job record is needed for pre-experiment segmentation, target checks and queued exports.
- Provider availability, SDK wire shapes and current pricing must be verified during phase 03. Source model identifiers are defaults, not proof of entitlement. Every paid path needs a reservation, finite limits and explicit live-test opt-in.
- P0 includes an actual Spark planning step, SAM perception, a Muse Image mutation and independent validity evidence. Adaptive feedback is P1. A bounded deterministic fallback is labeled honestly.
- Include a small random comparison in phase 06 because technical specification §11.4 requires it for P0; expand FR-17's P1 evaluation in phase 08.
- Screening does not count toward the three fresh confirmation calls. A known-broken target must fail the exported correctness assertion; a healthy target passes.
- Keep dollar amounts labeled actual, conservative estimates or unresolved charges. Discovery, precision, latency and sample-size goals are targets, never invented results.
- Documentation creation does not authorize paid calls, public deployment or submission. Future executors retain the source specification's approval and access boundaries for those actions.

## Functional requirement coverage

| Requirement | Owning phase | Supporting phase |
|---|---|---|
| FR-01 Project creation/reopening | 02 | 09 |
| FR-02 Upload/checksum/provenance | 02 | 03 |
| FR-03 Typed, human-reviewed contract | 01 | 02, 03 |
| FR-04 Approved target configuration | 03 | 02 |
| FR-05 Correct seed baseline | 05 | 03 |
| FR-06 SAM and correctable masks | 03 | 09 |
| FR-07 Controlled mutations | 04 | 03 |
| FR-08 Independent invariant validation | 04 | 05 |
| FR-09 Test only valid candidates | 05 | 04 |
| FR-10 Normalized output comparison | 05 | 01, 03 |
| FR-11 Fresh failure confirmations | 05 | 06 |
| FR-12 Executable regression export | 06 | 10 |
| FR-13 Evidence and cost dashboard | 06 | 05, 09 |
| FR-14 Query/time/dollar caps | 05 | 02, 03 |
| FR-15 Adaptive Spark planning | 07 | 08 |
| FR-16 Failure minimization | 07 | 04 |
| FR-17 Equal-budget random comparison | 08 | 06 |
| FR-18 Rejection explorer | 09 | 04, 05 |
| FR-19 Model-version retest | 07 | 06 |
| FR-20 CI integration | 11 | Future design gate |
| FR-21 Additional visual tasks | 11 | Future verifier designs |
| FR-22 Human-approved repair pipeline | 11 | Future design gate |

## Acceptance coverage

Each owner must implement the scenario, retain its result and make the outcome
available to phase 10. Supporting phases carry integration or release checks.

| Source test | Scenario | Owner / support |
|---|---|---|
| PRD-AT-01 | Verified counting seed stored and parsed | 02 / 03 |
| PRD-AT-02 | Incorrect original seed stops search | 05 |
| PRD-AT-03 | Removed protected object rejected | 04 / 05 |
| PRD-AT-04 | Ambiguous occlusion requires review | 04 |
| PRD-AT-05 | Valid background variant reaches target | 05 / 04 |
| PRD-AT-06 | Three pinned-setting retests saved | 05 |
| PRD-AT-07 | Partial reproduction is intermittent | 05 |
| PRD-AT-08 | Export executes against pinned target | 06 |
| PRD-AT-09 | Keys absent from browser/logs/export/repo | 10 / 02, 03, 06, 09 |
| PRD-AT-10 | Caps survive restart | 05 |
| PRD-AT-11 | README, licensing and local run | 10 |
| PRD-AT-12 | Accessible, accurately labeled demo | 10 / 09 |
| TS-AT-01 | Seed, source SHA and immutable VTC | 02 / 03 |
| TS-AT-02 | Wrong baseline prevents mutation search | 05 |
| TS-AT-03 | SAM count mismatch blocks approval | 03 |
| TS-AT-04 | Background-only edit validates | 04 |
| TS-AT-05 | Extra target object cannot become finding | 04 / 05 |
| TS-AT-06 | Ambiguous charged call quarantined | 05 / 03 |
| TS-AT-07 | Stable only after 3/3 fresh wrong counts | 05 |
| TS-AT-08 | Two of three wrong is intermittent | 05 |
| TS-AT-09 | Healthy target passes regression | 06 |
| TS-AT-10 | Broken target fails regression | 06 |
| TS-AT-11 | Duplicate experiment request is idempotent | 05 / 02 |
| TS-AT-12 | Budget exhaustion prevents another call | 05 |
| TS-AT-13 | Pause drains current call and stops new ones | 05 |
| TS-AT-14 | ZIP safe, complete and secret-free | 06 |
| TS-AT-15 | Private assets denied without authorization | 02 / 09 |
| TS-AT-16 | Public gallery is sanitized and read-only | 09 |
| TS-AT-17 | Equal-budget strategies transparently compared | 08 / 06 |
| TS-AT-18 | Real provider smoke evidence | 03 |
| TS-AT-19 | Outsider can inspect release and demo | 10 |
| TS-AT-20 | Asset and dependency rights documented | 10 / 08 |

## Running and cutting scope

Commands in phase documents are **targets to implement**, not existing commands.
Default tests use fixtures and no paid network access. Label live invocations,
cost limits and evidence separately. Check tasks only after their named tests and
exit gate pass; record implementation decisions and deviations with the phase.

If behind schedule, defer P2 first, then minimization, richer adaptive ranking and
visual polish. Preserve P0's real Meta use, independent validity review, bounded
execution, confirmations, export and visible evidence. Keep a small random
comparison. A missing real model failure is a disclosed unmet discovery goal,
not permission to relabel a fixture or a replay as a live discovery.

[Back to documentation](../README.md)
