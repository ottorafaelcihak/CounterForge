# Phase 11 — Post-MVP Roadmap and Design Gates

> **For agentic workers:** This is a roadmap, not authorization to implement unapproved P2 features. Use `superpowers:brainstorming` and `superpowers:writing-plans` to produce a separate decision-complete plan for a selected workstream.

**Goal:** Extend a verified MVP without weakening its ground-truth, authorization, provenance or budget guarantees.

**Architecture:** Keep the current pipeline as a frozen reference. New CI consumers reuse regression manifests, new providers implement the curated target interface, and new visual tasks supply their own versioned contracts and independent verifiers. Repair is a separate human-approved process downstream of evidence collection.

**Tech stack:** Reuse the existing repository and interfaces. Do not select new model, training, cloud or storage dependencies before the relevant design gate.

**Spec:** [PRD](../CounterForge_PRD_v1.0.md) FR-20–22, §§4, 21; [technical specification](../CounterForge_Technical_Specification_v1.0.md) §§1.3, 7, 9.5, 20.1, 23. This roadmap owns P2 coverage; the source does not define implementation-ready contracts for these features.

**Prerequisite:** [Phase 10](plan-10.md), retained release/evaluation evidence and an explicit choice of future workstream. **Target:** After MVP; no calendar commitment.

## Shared boundaries

- Preserve the existing counting VTC, export format and healthy/broken runner tests as compatibility fixtures.
- New target/provider access remains allowlisted, authorized, bounded and server-side.
- Every new task requires its own independent ground-truth verifier; a planner's claim cannot substitute for one.
- Model repair never happens silently after finding a weakness; training/data/deployment permissions are separate decisions.
- Multi-tenant SaaS, production billing, arbitrary target scanning, video and physical automation remain outside these workstreams.

## Workstream A: Regression CI integration — FR-20

**Depends on:** Portable runner, clean-environment checks and version retests from phases 06–07.

**Minimum interface:** A documented GitHub Actions workflow invokes the existing bundle runner with environment-supplied credentials and checksum-pinned artifacts. Core VTC semantics do not change.

- [ ] Define the supported repository/workflow trigger and credential trust boundary, including a no-secret fixture/replay mode for forks and pull requests.
- [ ] Choose opt-in live-run conditions, finite spend/runtime/concurrency caps and how CI execution costs are reported before implementing workflow dispatch.
- [ ] Design pass/fail/unknown infrastructure reporting so target regressions remain distinguishable from provider outages.
- [ ] Acceptance scenarios: healthy model passes; known broken target fails the correctness assertion; tampered bundle stops before dispatch; fork PR cannot obtain secrets or trigger paid inference.
- [ ] Approve the concrete workflow design and create a separate implementation plan before adding live CI.

## Workstream B: Additional visual tasks — FR-21

**Depends on:** Versioned contracts, review/evidence pipeline and auditable benchmark harness.

**Candidates:** Attribute matching, OCR or spatial relationships. Choose exactly one first; the source documents do not specify which has validated demand or reliable ground-truth preservation.

**Minimum interface:** A new task-specific contract, parser/comparator, allowed mutation registry, verifier and evaluation manifest. Keep `object_counting` compatibility and assign a new schema version when changing the existing discriminated contract.

- [ ] Specify real user examples, independently human-verified labels and ambiguous cases for the selected task.
- [ ] Define precisely which mutations preserve that answer and how violations are detected without using the target as judge.
- [ ] Freeze rights-cleared development/held-out cases, audit procedure and failure/reproduction semantics before optimizing search.
- [ ] Acceptance scenarios: valid preserved label accepted; changed semantic label rejected; unreadable/ambiguous evidence requires review; old counting bundles remain executable.
- [ ] Approve the task/verifier design and a separate implementation plan before expanding the API/UI.

## Workstream C: Additional authorized target providers

**Depends on:** Existing target protocol, accounting, version retest isolation and private credential handling.

**Minimum interface:** A new explicit registry adapter and provider/model identity, with scoped server-side credentials, strict output parsing, usage/pricing normalization and unknown-charge behavior. No generic user URL adapter is implied.

- [ ] Select the provider based on an authorized evaluation need; verify terms, model/version exposure, response shapes, rates and pricing from primary sources.
- [ ] Design a reviewed migration for the SQL `target_configs.adapter_key` and `provider` allowlists; the initial schema permits only Meta and fixture values.
- [ ] Define fixtures for quota, refusal, timeout, malformed output and provider-managed revisions; retain separate Meta core responsibilities.
- [ ] Acceptance scenarios: cross-provider retest preserves the exact label/image; credential scope does not leak; unsupported options fail visibly; unknown charged operations do not retry blindly.
- [ ] Approve the adapter design, budget and separate implementation plan before live provider access.

## Workstream D: Human-approved repair or retraining — FR-22

**Depends on:** Reliable findings, frozen regression suite, independent clean evaluation set and a model/pipeline the user is authorized to modify.

**Minimum interface:** An explicit proposal/approval/job/result boundary referencing immutable findings and dataset/model revisions. Changes do not mutate original ground truth or historical findings.

- [ ] Choose the concrete repair mechanism and authorized model ownership; do not assume hosted foundation-model weights are available for training.
- [ ] Define human approval, data licensing/privacy, compute/cost limits, rollback and release authority before any training or patch execution.
- [ ] Freeze both discovered regressions and an independent clean set. Compare pre/post correctness, uncertainty, cost and clean-set regressions; reject overfitting claims based only on discovered examples.
- [ ] Acceptance scenarios: no execution without approval; original model/results remain recoverable; a failed repair is recorded honestly; improvements cannot hide clean-set degradation.
- [ ] Approve a separate product/security/evaluation design and implementation plan before building the repair pipeline.

## Priority and completion

Default order is CI integration, one additional task, a justified additional
provider, then repair. These are independent future decisions, not prerequisites
for the MVP. Select one workstream at a time and preserve the frozen release.

- [ ] Every selected workstream has an approved design, explicit I/O and migration decisions, funded/authorized live access if needed and concrete acceptance tests.
- [ ] Untouched workstreams remain roadmap items, not partially implemented or claimed functionality.
- [ ] Add the selected workstream's detailed numbered plan to this directory and update [the roadmap](README.md) when its design gate passes.
