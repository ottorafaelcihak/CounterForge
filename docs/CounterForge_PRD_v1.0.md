# CounterForge — Product Requirements Document

**Version:** 1.0 — Hackathon MVP  
**Date:** 27 October 2026  
**Tagline:** *Find the failure. Prove the weakness. Prevent the regression.*  
**Competition:** Meta Global AI Developer Hackathon 2026  
**Recommended track / theme:** Indie Developer / Push the Frontier  
**Submission:** November 4, 2026, 9:00 PM US Eastern Time (November 5, 03:00 Europe/Madrid)  
**Status:** Approved design proposal; performance claims not yet tested

## 1. Executive summary

CounterForge is a software-only reliability laboratory for vision-language AI systems. A developer provides an authorized model endpoint, a seed image, a question and a human-verified correct answer. CounterForge designs controlled image perturbations, independently verifies that they preserve the test's intended ground truth, queries the target model, repeats suspected failures, and exports reproducible regression tests containing the assets, expected output, observed output, model/version, and mutation provenance.

**Product thesis:** AI should not only generate test cases; it should produce *valid, reproducible, actionable evidence* of model failure. The flagship innovation is a closed-loop pipeline: **plan → mutate → verify invariants → test → reproduce → minimize → export**.

**Hackathon MVP focus:** single-image object counting of visually distinct objects, with attribute verification if time permits. The system is not an automated model-repair platform, a generic image analyzer, or a claim that image fuzzing has never existed.

**Judge-facing pitch:** “CounterForge turns one correct AI answer into an autonomous search for its blind spots—and produces the exact tests needed to prevent those bugs from returning.”

## 2. Problem, customers and jobs to be done

Vision-language models may succeed on reference images but fail after lighting changes, background changes, clutter or partial occlusion. Manual edge-case collection is labor-intensive, random augmentation can miss meaningful failures, and unconstrained generative transformations can silently change the correct label. A reported ‘failure’ is worthless if its input no longer supports the original ground truth.

**Primary user:** AI engineer testing a computer-vision or multimodal feature before deployment. **Job:** “Given one representative successful case, find realistic nearby failure cases and give me evidence I can rerun in CI.”

**Secondary user:** QA/model-evaluation engineer comparing a candidate model version with a baseline. **Job:** “Track which validated edge cases regress, improve, or remain unresolved.”

**Future users, outside MVP:** robotics and industrial vision teams, hosted model providers, research labs and inference-platform developers.

## 3. Competition strategy and compliance

- **Problem statement:** Push the Frontier, because CounterForge develops autonomous software experimentation and model verification; Build for Your World is a secondary conceptual fit.
- **Meta requirement:** meaningful use of Meta Model API through Muse Spark, Muse Image and SAM 3.1. A token or ceremonial Meta API call is insufficient.
- **Judging weights in participant guide:** Impact 30%; Meta AI Tooling 25%; Technical Depth & Execution 20%; Demo 25%.
- **Mandatory submission:** public repository, clear identification of new work built during the hackathon, working product, accessible three-minute demo video, and team member attribution. Maximum team size four.
- **Anti-project avoidance:** CounterForge is not a simple image analyzer. Its defining feature is automated test design, independently validated synthetic variation, repeated evaluation and runnable regression artifacts.
- **Rights and safety:** only test owned or explicitly authorized target models; use original or appropriately licensed images, code and data; abide by service rate limits and model/platform policies. No political, war-related or prohibited-content test sets.
- **Preexisting tooling:** established dependencies and previously written components must be properly licensed/attributed; clearly separate newly developed competition contributions in commit history and README. Finalize details against any additional organizer rules.

## 4. Goals, success metrics and non-goals

### Product goals

G1. Discover at least one independently validated, repeatable vision-model error from a correct seed example.  
G2. Show that the mutation *preserves the expected answer* and attach evidence of that fact.  
G3. Make failure cases machine-readable and executable across future model versions.  
G4. Demonstrate meaningful use of Meta's planning, image-generation/editing and segmentation capabilities.  
G5. Provide a compelling, accurately presented three-minute demonstration.

### Proposed evaluation targets (not achieved results)

| Metric | MVP goal | Measurement |
|---|---:|---|
| End-to-end demo | 1 seed → 1 reproduced failure → 1 passing export execution | Recorded and live workflow |
| Seed cases | 12 labeled seeds | Dataset manifest, 8 development + 4 held-out |
| Candidate variants | 100 total target | Actual count and expense reported |
| Accepted-mutation precision | ≥90% in human-audited sample | Valid accepted candidates / audited accepted candidates |
| Strongest failure reproducibility | 3/3 incorrect responses | Same model/version/prompt/settings |
| Exported regression test | At least 1 executable test | Run in a clean environment |
| Baseline | Random/untargeted augmentation at equal query budget | Confirmed failures per valid model call |
| Provenance | 100% of confirmed findings | Inputs, revisions, model identifiers, timestamps, hashes |

A target missed is reported honestly, not retroactively labeled a success. Small-sample results must include denominators and uncertainty.

### Explicit non-goals for MVP

No live physical hardware, general-purpose web crawling, authentication to arbitrary external systems, automatic safety certification, generalized video-fuzzing, automatic foundation-model fine-tuning, production SaaS billing, mathematical proof of invariants, or claimed causal explanations of model internals. Detection is distinct from repair.

## 5. User journey and product experience

1. **Create project:** enter task name and choose `object_counting`; configure an authorized target-model adapter and spending/query limits.
2. **Provide seed:** upload an original/licensed image, a canonical question, a structured expected answer and a label-verification acknowledgement.
3. **Define contract:** select protected object class and quantity; select allowed mutation families and forbidden changes; preview masks and correct them if needed.
4. **Run baseline:** query the target with the unchanged seed; only start search if the expected answer is returned consistently, otherwise mark `SEED_INVALID_FOR_SEARCH`.
5. **Start experiment:** Muse Spark proposes a bounded batch of likely failure-inducing changes; mutation engine creates images and records its exact recipe.
6. **Validate candidates:** invariant verifier returns `VALID`, `INVALID`, or `REVIEW_REQUIRED`. Only `VALID` images go to automated target testing; `REVIEW_REQUIRED` waits for a human decision.
7. **Evaluate:** normalize answer and compare to verified ground truth. On suspicious results, rerun using the same pinned model settings; classify stable versus intermittent errors.
8. **Explore and minimize:** optionally test simpler variants of the promising failure and retain the smallest validated trigger found.
9. **Export:** emit JSON manifest, images, hashes and a Pytest runner. Users can rerun the bundle against a newer model version.
10. **Inspect results:** dashboard visualizes original-vs-mutated images, SAM masks, validation evidence, model answers, rates, cost and limitations.

## 6. Functional requirements

Priority definitions: **P0** = required for valid hackathon demo; **P1** = high-value differentiator; **P2** = post-MVP/stretch.

| ID | Priority | Requirement | Acceptance condition |
|---|---|---|---|
| FR-01 | P0 | Create and reopen a test project | Persist name, owner/local-user, task type and limits |
| FR-02 | P0 | Upload and checksum a seed image | PNG/JPEG, MIME/size check, SHA-256, provenance record |
| FR-03 | P0 | Specify a contract and human-reviewed ground truth | Typed schema validated; no free-form unverified answer accepted |
| FR-04 | P0 | Register approved target-model adapter | Test connection, store secret server-side, pin version/settings |
| FR-05 | P0 | Verify the seed passes the baseline target model | Halt search with actionable error if it does not |
| FR-06 | P0 | SAM 3.1 segmentation and protected-mask preview | Masks stored, displayed and user-correctable |
| FR-07 | P0 | Generate controlled image mutations | At least deterministic lighting/background changes plus Muse Image-supported context variation |
| FR-08 | P0 | Independently validate invariants | Record VALID / INVALID / REVIEW_REQUIRED and supporting checks |
| FR-09 | P0 | Test only validated candidates | No INVALID candidate may count toward a confirmed finding |
| FR-10 | P0 | Compare normalized output to expected answer | Store raw response, parsed answer, evaluator version |
| FR-11 | P0 | Repeat suspected failures | Capture three runs and classify stable / intermittent / not reproduced |
| FR-12 | P0 | Export and execute regression artifacts | Download manifest+images+Pytest; reproduce known mismatch |
| FR-13 | P0 | Show evidence and costs in dashboard | Original/mutation, protected mask, test verdict and API usage |
| FR-14 | P0 | Enforce experiment query, time and dollar budgets | Stop scheduling before configured limits; log stop reason |
| FR-15 | P1 | Adaptive Spark experiment planner | Next batch uses prior outcomes and explicit hypotheses |
| FR-16 | P1 | Reduce failing mutation to a simpler one | Keep only reduced candidates that remain VALID and failing |
| FR-17 | P1 | Random-augmentation baseline comparison | Equal evaluation budget with reproducible seed |
| FR-18 | P1 | Candidate rejection explorer | Show exactly why an attempted edit was invalid |
| FR-19 | P1 | Retest suite against model version B | Compare reproducibility without rewriting ground truth |
| FR-20 | P2 | GitHub Action/CI integration | Documented workflow with optional model credentials |
| FR-21 | P2 | Additional tasks: attribute matching, OCR, relation understanding | Each task has its own explicit ground-truth verifier |
| FR-22 | P2 | Human-confirmed patch/retraining pipeline | Compare pre/post repair accuracy, including clean-set regression |

## 7. Visual Test Contract (VTC)

The VTC is the central, versioned product artifact. It defines what may vary and what must stay true. Sample:

```json
{
  "schema_version": "1.0",
  "case_id": "inventory-count-001",
  "task_type": "object_counting",
  "question": "How many blue containers are visible? Reply with an integer.",
  "expected": {"type": "integer", "value": 5, "verified_by": "human"},
  "protected": {"category": "blue container", "count": 5, "minimum_visible_fraction": 0.95},
  "allowed_mutations": ["background_replacement", "background_clutter", "bounded_brightness"],
  "forbidden_mutations": ["object_addition", "object_removal", "semantic_label_change"],
  "validation": {"mask_integrity_required": true, "ambiguous_cases": "review_required"},
  "reproduction": {"attempts": 3, "required_failures": 3},
  "limits": {"max_candidates": 20, "max_target_calls": 60, "max_estimated_usd": 10.0}
}
```

Numbers above are configuration examples, not universally proven thresholds. VTCs are immutable once an experiment starts; changes create a new version and preserve history.

### Candidate validity outcomes

- **VALID:** deterministic constraints pass, protected content/labels are preserved at required confidence, and there is enough evidence to evaluate the task.
- **INVALID:** a protected object is missing/modified, the image fails decoding, the transformation violates allowed regions, or the human-verified answer is no longer valid.
- **REVIEW_REQUIRED:** the verifier cannot confidently establish preservation (e.g., SAM uncertainty, ambiguous visibility or difficult scene semantics). Do not automatically count as a failure.

For object counting, prefer object-preserving compositing: retain protected source-object pixels and masks; generate or replace only the unprotected background. Use deterministic perturbations for brightness, contrast and controlled clutter when they better preserve labels. SAM supplies segmentation evidence but is not itself proof that the count and semantics survived; perform manual auditing of sampled candidates.

## 8. Agent and experiment state machine

**Experiment states:** `DRAFT → BASELINE_VALIDATING → READY → PLANNING → MUTATING → VALIDATING → EVALUATING → CONFIRMING → COMPLETED`, with interrupt states `PAUSED`, `BUDGET_EXHAUSTED`, `FAILED`, `CANCELLED`. A candidate follows `CREATED → MUTATED → VALID|INVALID|REVIEW_REQUIRED → EVALUATED → SUSPECTED_FAILURE → REPRODUCED_STABLE|INTERMITTENT|NOT_REPRODUCED → EXPORTED`.

**Planner input:** VTC, seed descriptors, prior hypothesis/mutation/outcome records, remaining budget, allowed tools. **Planner output:** a typed mutation plan with family, rationale, strength and cost. Reject proposals that violate the VTC before executing any API call.

**Selection:** start with a balanced small batch of deterministic and Muse-generated mutations. In P1, optimize *confirmed valid failures per target-model call* under a limited exploration budget rather than blindly maximizing surprising pictures. Never silently change the expected answer. Store random seeds where the generating method supports determinism, but always retain resulting assets because generative APIs may not replay identically.

**Failure interpretation:** a model error is an incorrect answer *to a valid test* for a specified model/version/prompt/configuration. A consistent error across three repeated calls is `STABLE_FOR_3_TRIALS`, not proof of 100% reproducibility. Explanations are hypotheses unless supported by controlled comparisons.

## 9. AI integration requirements

| Service | Current model ID | Function |
|---|---|---|
| Meta Muse Spark | `muse-spark-1.3` | Image/task understanding, mutation planning, result summary |
| Meta Muse Image | `muse-image-1.0` | Generative scene/background variation |
| Meta SAM 3.1 | `sam-3.1` | Foreground masks and geometry evidence |
| Target model | User-authorized adapter, initially Meta Spark or a local benchmark model | Model under test; must be clearly distinguished from evaluator |
| Optional Muse Wonder | `muse-wonder-1.0` | Complex retrospective reasoning, not a critical-path dependency |

**Base URL:** `https://api.meta.ai/v1`. **Auth:** server-side `MODEL_API_KEY`. Spark and SAM use Meta's Responses interface; Muse Image exposes image generation/edit routes. Isolate each API in an adapter that validates response shape, handles timeouts/retries, measures latency/usage, and exposes no credentials to the browser. Confirm exact supported request structures with the live SDK/docs before coding; do not hardcode speculative payload fields.

**Independence:** Muse Spark may propose and summarize tests but must not be the only judge of mutation validity. Use source-image masks, image-level invariants, task-specific parsers, manual evidence review and deterministic rules. When target and planner share a model family, explicitly disclose the relationship and test for evaluator bias.

## 10. Reference architecture and repository

**Frontend:** Next.js, React, Tailwind CSS.  
**API:** Python 3.12+, FastAPI, Pydantic.  
**Workers:** Redis + RQ (or a single bounded async worker if deployment simplicity wins).  
**Persistence:** PostgreSQL and local object store/MinIO.  
**Image utilities:** OpenCV, Pillow, NumPy.  
**Testing:** Pytest, contract tests, Playwright smoke tests.  
**Deployment:** Docker Compose on Ubuntu; heavy inference hosted by Meta. No required new hardware, Kubernetes or model fine-tuning.

```text
counterforge/
  apps/web/                 # UI, pages, evidence viewer
  services/api/             # FastAPI endpoints and schemas
  services/worker/          # bounded orchestration and evaluation jobs
  packages/contracts/       # VTC Pydantic schemas, JSON schemas
  packages/meta_adapters/   # Spark, Muse Image, SAM clients
  packages/mutations/       # compositing and deterministic perturbations
  packages/verification/    # invariants, answer parsing, validity verdicts
  packages/targets/         # authorized target-model adapters
  packages/regression/      # export/test-runner logic
  evaluations/              # labeled seeds, baseline and held-out manifests
  tests/                    # unit, contract, integration, E2E
  docs/                     # README, architecture, demo, limitations
  docker-compose.yml
  .env.example
```

### Core API surface (proposed application endpoints)

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/v1/projects` | Create project |
| POST | `/api/v1/projects/{id}/seeds` | Upload image and register verified expected value |
| POST | `/api/v1/projects/{id}/contracts/validate` | Validate a VTC draft |
| POST | `/api/v1/targets/validate` | Check an authorized target-model adapter |
| POST | `/api/v1/experiments` | Start bounded search from approved seed/VTC |
| GET | `/api/v1/experiments/{id}` | Retrieve state and aggregate usage |
| GET | `/api/v1/experiments/{id}/candidates` | Paginated mutations and verdicts |
| GET | `/api/v1/experiments/{id}/events` | Server-sent progress events |
| POST | `/api/v1/experiments/{id}/pause` | Stop new API calls after current jobs finish |
| POST | `/api/v1/experiments/{id}/resume` | Continue within remaining budget |
| GET | `/api/v1/experiments/{id}/failures` | Evidence-backed discoveries |
| POST | `/api/v1/failures/{id}/confirm` | Repeat target-model evaluation |
| POST | `/api/v1/failures/{id}/export` | Build downloadable regression bundle |
| POST | `/api/v1/regressions/{id}/run` | Execute an existing test against selected target |
| GET | `/api/v1/evaluations/{id}/report` | Aggregate metrics and baseline comparison |

Every endpoint uses validated inputs and normalized error envelopes (`code`, `message`, `correlation_id`); long calls enqueue work, return IDs and expose status; mutations and targets cannot be created from raw unaudited URLs by default.

### Data model

- `projects(id, name, owner_id, created_at, settings_json)`
- `target_configs(id, project_id, adapter_type, model_id, version, config_json, secret_ref)`
- `seed_cases(id, project_id, image_object_key, sha256, question, expected_json, label_provenance)`
- `visual_contracts(id, seed_id, version, contract_json, created_at, locked_at)`
- `experiments(id, contract_id, target_config_id, state, max_budget_usd, spent_usd, stop_reason, created_at)`
- `mutations(id, experiment_id, parent_id, family, plan_json, image_object_key, sha256, strength, state)`
- `validations(id, mutation_id, verdict, checks_json, reviewer_id, timestamp)`
- `model_observations(id, mutation_id, target_config_id, attempt, raw_output_key, parsed_output_json, is_correct, latency_ms)`
- `failures(id, experiment_id, mutation_id, status, reproduction_n, reproduction_failures, summary_json)`
- `regression_cases(id, failure_id, manifest_key, bundle_key, runner_version, created_at)`
- `usage_events(id, experiment_id, provider, model_id, tokens_in, tokens_out, estimated_usd, timestamp)`
- `audit_events(id, actor, action, entity_type, entity_id, context_json, timestamp)`

Foreign keys protect provenance. Large images, masks and raw outputs live in object storage; DB holds references and SHA-256 integrity hashes. Secret references point to environment/secrets storage, never plaintext database/API responses.

## 11. UI screens

1. **Overview:** projects, latest experiment, valid mutation rate, confirmed failure count, spend.
2. **Seed & contract setup:** upload, task definition, ground-truth declaration, SAM mask preview/manual adjustment, permitted mutation toggles, model choice, budget.
3. **Experiment cockpit:** state, queue, progress, candidate gallery, hypothesis history, per-stage failures, pause/resume.
4. **Failure evidence:** side-by-side original/variant, difference/highlight overlay, ground-truth checks, three target responses, model identity, provenance and export action.
5. **Regression library:** bundles, retest against version B, pass/fail/change history.
6. **Evaluation dashboard:** confirmed failures per valid call, invalid rate, random-baseline comparison, costs, failure taxonomy, known limitations.

The user should understand the main failure without reading logs; evidence and uncertainty labels remain visible. Responsive desktop-first interface; basic keyboard navigation, captions/alt text and contrast support.

## 12. Nonfunctional requirements

- **Security:** keys server-side only; safe upload validation; block SSRF/private-network calls from arbitrary model endpoints; explicit target allowlist; request budgets, rate limiting, payload caps and path sanitization.
- **Privacy and rights:** synthetic/original/licensed media; do not include actual confidential customer data in the public submission. Demo data public and releasable; disclose third-party services and data flows.
- **Reliability:** idempotent jobs; retry transient failures with backoff; no double billing from unintended retries; workers persist state; interruption/restart resumes safely.
- **Reproducibility:** store model ID/version, prompt hash, evaluator revision, inference parameters, image SHA-256 and raw response; version all schemas.
- **Usability:** clear status, budget meter, invalid-candidate explanation, export guidance and graceful API-error handling.
- **Performance:** responsive UI even during long inference; latency and cost measured rather than guaranteed. Avoid model-call concurrency above provider limits.
- **Ethics:** authorized benign evaluations only. Avoid harmful requests and instruction-hijacking use cases; prompt/image content is untrusted input. No claims of formal safety certification.

## 13. Evaluation methodology

**Task set:** create 12 original/licensed labeled counting cases: 8 for development and 4 frozen held-out. Capture varied counts, object sizes, scene complexity and backgrounds. Require that unchanged seed images pass the baseline target before testing nearby mutations; report excluded cases separately to avoid artificially inflated success rates.

**Comparison:** same seed pool and target-model query budget for (A) random/untargeted perturbations and (B) CounterForge's planned search. Both pipelines must apply the same invariant verifier and confirmation rule. Do not count invalid variants toward confirmed failures; report all generation failures and expenses in denominators.

**Primary outcome:** independently confirmed stable wrong answers per 100 **valid** candidate target calls, alongside total generated candidates and dollar cost per confirmed failure. Report overall error detection and valid-candidate yield separately to avoid optimizing away expensive invalid candidates.

**Secondary outcomes:** invalid mutation rate; manual-audit precision of accepted mutations; percent failures reproduced 3/3; lower-bound mutation strength achieved by minimization; p50/p95 API latency; estimated total API cost; exported test execution rate.

**Ablations:** (1) random mutations, (2) Spark planning without SAM-based object preservation, (3) full CounterForge. Discuss invalid-case contamination where ablations lack a verifier; never present their findings as equally trustworthy.

**Data integrity:** freeze held-out assets and human labels before final tuning; store prompts and evaluator revisions. Do not cherry-pick favorable results in aggregate reporting. Use several named illustrative cases for a reliable, clearly labeled demo.

## 14. Acceptance tests and definition of done

- **AT-01:** A valid counting seed is uploaded, labeled, stored and reliably parsed.
- **AT-02:** A seed the target already gets wrong is rejected as an unsuitable *success-to-failure* baseline, with a visible reason.
- **AT-03:** Removing a protected object results in `INVALID` and no confirmed failure.
- **AT-04:** Occlusion that makes ground truth ambiguous results in `REVIEW_REQUIRED` rather than a fabricated success.
- **AT-05:** A valid changed-background image retains the contract answer and is submitted to the target.
- **AT-06:** A suspected wrong answer triggers three pinned-setting retests and stores every raw result.
- **AT-07:** A case failing only one of three trials is labeled intermittent, not stable.
- **AT-08:** A confirmed stable case exports a bundle; executing the provided test against the pinned target reproduces the mismatch (unless provider nondeterminism has changed; record deviation).
- **AT-09:** API keys never appear in browser payloads, logs, exports or the public repository.
- **AT-10:** Query and cost caps stop new jobs even after a restart.
- **AT-11:** Public repo README identifies all new work, third-party licenses, model limitations and how to run locally.
- **AT-12:** Three-minute demo and URL work from a clean, unauthenticated reviewer environment where feasible; private API keys are not exposed.

**MVP is done** only when AT-01 through AT-11 pass, one complete genuine failure pipeline works, a regression export runs, and the submitted materials accurately distinguish live from recorded/cached outputs. A polished interface cannot substitute for those criteria.

## 15. Risks and contingencies

| Risk | Why it matters | Mitigation / fallback |
|---|---|---|
| Generative edit changes the true answer | False bug reports | Deterministic compositor and invariant rejection; use fewer Muse Image transformations |
| SAM imperfect masks | Wrong protected regions | Mask preview/manual repair; audit; restrict to distinct object shapes |
| No target failures found | Weak demo | Start with plausible known failure families; increase clutter/lighting difficulty within validity limits; disclose search results honestly |
| Target nondeterminism | Irreproducible assertions | Pin settings, repeat, classify intermittent, allow robust comparator |
| API quotas/credits | Experiment stalls | Strict budgets, caching, small image batches, early credits check |
| Too many invalid candidates | Cost and schedule risk | Switch to deterministic augmentation and background-only compositing |
| Planner adds no lift over random | Core novelty unproven | Report ablation and strengthen hypothesis feedback loop; do not invent superiority |
| Late integration | No working submission | Freeze scope October 30; one happy path before polish |
| Existing research overlap | Unsupported originality claims | Frame novelty as integrated verified failure-to-regression loop, cite prior fuzzing methods |

## 16. Delivery plan, October 27–November 4

| Date | Main outcome | Exit gate |
|---|---|---|
| Oct 27 | Repo, VTC schema, test dataset, Meta/target adapter handshake | One correct seed round trip |
| Oct 28 | SAM masks, human label review, deterministic mutation engine | Valid/invalid examples identified |
| Oct 29 | Muse Image scene mutations, storage and validator | Source ground truth survives accepted edits |
| Oct 30 | Target evaluator, repeat detection, first complete failure export | **Critical gate: end-to-end pipeline works** |
| Oct 31 | Spark planner with bounded feedback loop | Typed plans and genuine adaptive next-step choice |
| Nov 1 | Failure minimizer, JSON/Pytest bundles | Exported test executes in clean environment |
| Nov 2 | Random baseline, held-out tests, result audit | Evidence-backed metrics with denominators |
| Nov 3 | Dashboard polish, docs, video dry runs | Stable rehearsed demo and security review |
| Nov 4 | Public repo, three-minute video, accessible links, submit | Submission confirmed before deadline |

**If behind schedule:** prioritize baseline → deterministic image change → invariant proof → target failure → repeat → export. Defer minimization, multi-model comparisons, automated repair, detailed dashboard analytics, and multi-tenant accounts. Maintain real Meta model usage via Spark planning and SAM perception; integrate Muse Image meaningfully before final submission if the experiment pipeline is robust.

## 17. Demo storyboard (three minutes)

- **0:00–0:20 — Problem:** Model correctly counts objects in a seed image; visually similar images may break the answer.
- **0:20–0:50 — Contract:** Show verified count, protected objects, accepted mutation categories and target model.
- **0:50–1:35 — Autonomous loop:** Spark chooses controlled changes; Muse Image and SAM-backed processing generate and filter them. Display `INVALID` rejection as well as `VALID` evidence.
- **1:35–2:10 — Real discovery:** Show one validated image with unchanged ground truth but an actual recorded wrong answer; demonstrate repeated result.
- **2:10–2:40 — Actionable outcome:** Export JSON + image + runnable Pytest; run the test to show a legitimate regression finding.
- **2:40–3:00 — Impact:** Show measured comparison to random mutation, number of valid candidates, cost and applicability to other vision AI products.

Use a curated genuine run and clearly label replay/cached data. Keep a second validated failure case as demo fallback. Do not manufacture or fabricate performance numbers.

## 18. Product epics and parallel workstreams

**Epic A — Contracts and evaluation core (critical path):** schemas, comparators, immutable evidence, validation states, failure reproducibility. Ownership: backend/evaluation agent.

**Epic B — Meta model integrations:** tested Spark, SAM and Muse Image adapters, request/response fixtures, retry/budget handling. Ownership: model-integration agent.

**Epic C — Mutations and invariant verification:** foreground preservation, background generation/compositing, OpenCV transformations, mask QA and human-review path. Ownership: vision agent.

**Epic D — Orchestration and API:** queues, state machine, persistence, APIs, artifact storage, cancellation, audit records. Ownership: backend/platform agent.

**Epic E — Product UX:** seed wizard, contract editor, experiment cockpit, failure inspector, results and downloads. Ownership: frontend agent.

**Epic F — Validation, research and submission:** dataset manifests, random baseline, adversarial quality review, acceptance tests, reproducible README, demo production, originality/license audit. Ownership: QA/research agent.

Parallel agents must share the VTC JSON schema, target-adapter interface, response error envelope and database migrations before implementing features; API-contract tests guard integration. A single integrator owns merges and deployment.

## 19. Cost and deployment guardrails

No additional physical hardware is required for MVP if hosted Meta models are used. Use existing Ubuntu server for API, workers, DB and frontend. Claim eligible Meta hackathon credits if available under the participant guide; adding a payment method may be required. Configure provider alerts plus a stricter application-side request/cost cap because provider alerts alone may not stop spending. A proposed development allowance is **$15–60 of API usage**, not a promise or quoted total. Record real usage by provider/model and export a final spend summary.

Demo deployment: Docker Compose, environment-based secrets, TLS/reverse proxy if publicly hosted, clean public README, sample openly licensed test images, live-mode access controlled to avoid public abuse of paid API keys. Judges can view video/results without consuming uncontrolled API credits.

## 20. Submission checklist

- [ ] All showcased core functionality was newly developed during the event and commits make this clear.
- [ ] Repo is public, dependency licenses/attributions documented; no credentials or confidential data committed.
- [ ] Live prototype and/or reproducible local-run instructions work.
- [ ] Three-minute demo is accessible and clearly shows real Meta-powered functionality.
- [ ] Team credits, model/tool disclosures, all submission form fields completed.
- [ ] At least one stable failure has verified ground truth, raw responses and replayable regression bundle.
- [ ] Aggregate results compare against a fair baseline and disclose failures/limitations.
- [ ] Demo data, images and model endpoints are authorized for release/testing.
- [ ] Submit by November 4, 9 PM ET (November 5, 03:00 in mainland Spain).

## 21. Open decisions and product boundaries

1. **Target model:** default to a benchmark-friendly vision model we are authorized to call, preferably one with pinned settings and manageable cost. This decision should not block building the adapter contract.
2. **Image domain:** choose blue-container/shelf counting or another easily labeled object-counting dataset with licensed images and visible ground truth. Restrict to one domain until the full loop works.
3. **Deployment access:** public read-only gallery and restricted experiment creation versus local demo only. Default to restricted creation to protect API credits.
4. **Human verification:** require manual acceptance when preservation is ambiguous; do not replace uncertainty with a model-generated assertion.
5. **Stretch priorities:** adaptive planning before broader task support; reproducibility before model repair.

## 22. Relevant sources

**Official Meta developer docs:**
- https://dev.meta.ai/docs/overview
- https://dev.meta.ai/docs/models
- https://dev.meta.ai/docs/sam/overview
- https://dev.meta.ai/docs/media-segmentation

**Event and participant materials:**
- https://dev.meta.ai/events/global-hackathon
- https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details (participant-only guide content provided by entrant)

**Important novelty note:** model fuzzing, adversarial testing, image generation and synthetic test data already exist. CounterForge's proposed distinctive contribution is an integrated, explicit *test-validity contract → adaptive failure search → reproducibility → executable regression* system. Novelty and commercial differentiation must be established through measured behavior, not a claim that no comparable idea has ever existed.
