# Phase 04 implementation handoff

**Date:** 2026-10-29. **Branch:** `master`. **Base:** `e2d6358` (Phase 03,
already pushed). Phase 04 implementation and synthetic fixture gates are verified.
**Actual human specimen acceptance, automatic threshold calibration and inherited
live-provider smoke TS-AT-18 remain unmet.** No application live Meta call was
dispatched, target evaluated or real-model weakness claimed.

## Delivered behavior

- Exactly three typed, VTC-approved families: background replacement, background
  clutter and brightness shift. Recipes retain source/contract/mask/output hashes,
  exact integer parameters, four-decimal strength rounding, PRNG seed and software
  versions. Deterministic replay is verified; viewing uses retained image bytes.
- Strict binary original foreground compositing, including contours. Background,
  protected union and absolute pixel diff are retained. Interior and one-pixel
  inner/exterior contour measurements are separate. No feathering is permitted.
- Independent versioned checks for media, provenance, recipe, masks, geometry and
  protected pixels. Mandatory FAIL means INVALID; unresolved mandatory evidence
  means REVIEW_REQUIRED. SAM is supporting evidence and cannot establish the
  human count. Human semantic and visibility decisions are always required.
- Immutable candidate evidence and experiment snapshots; append-only ordered
  validation, audit and request-alias history. Actor identity comes from owner
  authentication. Notes are sanitized before report sealing. Review binds exact
  prior-report and evidence hashes; stale decisions return 409.
- Failed human checks survive candidate SAM refresh, including reviews arriving
  while SAM runs. A favorable review of an INVALID candidate needs an explicit
  ground-truth audit resolving named failed human checks. Structural FAILs cannot
  be overridden. Ambiguity stays unresolved; confirmed destroyed visibility fails.
- One canonical retained output per experiment. Different keys/plans/seeds that
  produce that same image return its original recipe and latest review history,
  preserving rejection and consuming no additional candidate slot. Request aliases
  retain every distinct request. Same-key changed requests return 409.
- Bounded SCENE and MUTATION_SAM preparation jobs use the existing committed
  allowance, reservations, provider lease, claim/fence, runtime and recovery path.
  Raw and normalized backgrounds and candidate segmentation evidence are retained.
  Untrusted provider images decode in a bounded, credential-free subprocess.
- Authenticated `/mutations/{id}` evidence screen displays original, candidate,
  background, union, diff, source masks, optional SAM masks, named checks, fixed
  human count, recipe/hashes, review form and revision/audit history. Missing images
  and stale reports block submission. Drafts survive session renewal and cancelled
  navigation/reload. INVALID and REVIEW_REQUIRED have distinct states and reasons.

## API and Phase 05 integration

All paths below are under `/api/v1`, owner authenticated. POSTs require an
`Idempotency-Key` where specified by OpenAPI. The web proxy exposes only candidate
GET/history and review POST; preparation and creation currently use the API.

| Endpoint | Input / result |
| --- | --- |
| `POST /experiments/{id}/mutations` | `{plan, rng_seed, background_job_id?}`; retained candidate with initial report |
| `GET /experiments/{id}/mutations` | Scoped, paginated candidate list and optional state filter |
| `GET /mutations/{id}` | Candidate, current report, authenticated asset IDs and hashes |
| `GET /mutations/{id}/history` | Paginated ordered report revisions, actors and full audit references |
| `POST /mutations/{id}/review` | `{prior_report_sha256, evidence_hashes, semantic, visibility, notes, ground_truth_audit?}` |
| `POST /contracts/{id}/backgrounds` | `{plan, limits}` for a generative background plan; durable JobRef |
| `POST /mutations/{id}/segment` | `{limits}`; durable supporting-segmentation JobRef |

`semantic` is consistent/additional_object/removed_object/ambiguous; `visibility`
is clear/ambiguous/destroyed. An audit contains `resolved_check_codes` and `note`;
only failed HUMAN_SEMANTICS/HUMAN_VISIBILITY checks can be resolved. Reports bind
the retained full audit document. This single-owner application permits that
owner to audit an earlier decision; no second-person approval is claimed.

Background creation precedes candidate creation. Pass a completed owned SCENE job
with the same allowed plan and immutable source/contract/mask identity. Approval
timestamps and reviewer changes on identical reviewed mask bytes do not discard
an already prepared background. Both snapshots are independently validated.

Phase 05 must call `Service.require_valid_candidate` under the dispatch transaction
before target work, using the latest sealed evidence. Unsupported verifier/policy
versions fail closed as REVIEW_REQUIRED. No baseline, scheduler, candidate target
call, confirmation loop or finding promotion is implemented in Phase 04.

Preparation jobs have no experiment ID and consume the deployment preparation
allowance. They do not populate experiment spend. Phase 05 must integrate this
accounting deliberately rather than imply that all experiment costs are already
included. Candidate creation increments `candidates_created` once per unique
image and considers pending reservations; avoid double-counting reserved slots.

## Verification

Coordinator-executed against the integrated checkout, using disposable PostgreSQL
and Redis and isolated fixture processes:

| Check | Result |
| --- | --- |
| Unit and contract tests, sockets blocked | **298 passed**, 6.20s |
| Full PostgreSQL integration suite, explicit loopback test DB | **139 passed**, 120.73s |
| Real-process Chromium seed setup and candidate review | **2 passed**, 15.63s |
| Focused final review/migration regressions | **28 passed**, 29.86s |
| Ruff check / format check | Passed, 138 Python files |
| Strict mypy | Passed, 76 source files |
| Web ESLint / TypeScript / production build | Passed |
| OpenAPI, nine JSON schemas and generated TypeScript drift | Passed |
| Offline uv lock consistency | Passed, 69 packages |
| Compose config, placeholder values and `/dev/null` env file | Passed |
| API, worker and standalone web image builds | Passed |
| New API/worker module imports in packaged images, network disabled | Passed; Pillow 13.0.0 |
| Git whitespace check | Passed |

The candidate browser case uses real Next/FastAPI/PostgreSQL processes with
synthetic approvals. It exercises REVIEW_REQUIRED → VALID → human INVALID →
explicitly audited VALID → ambiguous REVIEW_REQUIRED → destroyed-visibility INVALID,
fixed count five, seven retained revisions, stale-report rejection and cancelled/
accepted reload, missing-image blocking, session-expiry draft preservation, route/
query/Origin rejection, sign-out asset denial, zero target observations and
390-pixel overflow. Desktop, form and mobile screenshots were visually inspected.
The existing seed workflow also runs real Redis/RQ and fixture SDK/parser paths.

Meaningful regressions reproduced duplicate rejection laundering, unchanged-mask
reapproval mismatch and legacy duplicate-image migration/lookup failures before
fixes. Browser checks reproduced unstable exact labels; explicit label references
fix them. The missing-image test starts a new document to avoid decoded-image
memory caching. An initial combined test command allowed loopback for unit tests,
so the no-network guard intentionally failed; separate socket-blocked unit and
loopback-only integration runs above are authoritative.

Tests create random DB schemas, their own queue, artifacts and service processes;
no application database fallback or shared Redis flush is permitted. Disposable
task-owned containers are removed after verification. SDK/thread-backed tests
require sandbox escalation in this environment; pytest provider socket blocking
remains enabled. No real `.env` or credentials were read for verification.

## Review rulings and retained specimens

- Pillow integer LUTs, binary compositing and morphology cover the needed work;
  adding NumPy/OpenCV would not improve these operations. Pillow is pinned to
  13.0.0. Structural comparison uses decoded pixels so harmless PNG recompression
  does not become a false invariant failure; byte hashes still bind exact assets.
- No calibrated safe feather, semantic or visibility threshold exists. The strict
  binary policy preserves foreground and measures its contour; explicit human
  decisions resolve remaining semantic and visibility uncertainty. Policy is
  `strict-binary-human-gates-1`, verifier `phase04-1`.
- Strength is a family parameter, not a shared severity scale. Brightness uses
  gain `0.5 + strength` (identity at 0.5); replacement is identity at zero; clutter
  already draws one rectangle at zero. Phase 05 must not call a no-op a new
  weakness. Phase 07 must refine toward each family's actual neutral operation.
- Migration 0004 preserves the original supplied SQL and existing legacy rows,
  including duplicate historical images. Only sealed candidates enter canonical
  deduplication; unsealed legacy reports remain nullable and cannot authorize
  dispatch. Downgrade refuses to discard append-only evidence.

[Retained specimens](../../tests/fixtures/phase04/README.md) include an accepted
background after **simulated** review, known object removal, ambiguous visibility
and seam-only control, with bytes, recipes, metrics and hashed reports. Their
manifest says `actual_human_review=PENDING` and `live_provider=false`. They are
development fixtures, not calibrated thresholds or held-out evaluation data.
Seam comparison remains Phase 08. PRD-AT-03–04 and TS-AT-04–05 have fixture
coverage; PRD-AT-05's target submission is owned by Phase 05 and is not claimed.

## Three-provider participation

The coordinator integrated and independently verified the result. Editing workers
were serialized in the shared `master` checkout; terminal status and stopped
supervisors were confirmed before editor ownership changed. No worktree or PR.

| Harness / actual model | Focused work |
| --- | --- |
| Codex / `gpt-6.1-sol` | Pure packages (`093abfde-1521-4a61-aa34-01ec817cd15f`) and backend integration (`83581977-6eed-4913-8520-2bf634b64531`), with targeted checks. Other attempts hit quota/capacity limits; completed code was preserved. |
| Claude Code / `claude-opus-5` | Independent package review (`2e7cf2d3-4694-469f-ad67-53c628dfdfc8`), backend review (`3b988af0-7843-440a-9ef5-a47fe3cb640d`) and serialized final fixes (`43ae638d-5b6c-4ea2-a563-b7f1d38efc01`). The final editor's Bash checks/inbox required approval, so the coordinator cancelled after edits, confirmed supervisor stopped and performed verification. No blocked worker check is claimed. |
| Muse / Wonder 1.0 configured CLI default | Acceptance preflight and final read-only integration review (`7e69bc9c-deed-42d7-832c-d0fb106a9efe`); metadata records `muse-wonder-1.0` / contributor routing. No material defects; browser gaps prompted stale/image/session/proxy checks. Shell was unavailable, so no Muse-run test/build is claimed. |

Bridge 1.1.0 and the installed develop-with-agent-team workflow were used.
The coordinator implemented the evidence UI, additional regressions and legacy
compatibility fix. Review outputs were inspected and material findings resolved.

## Carry-forward gates

Actual authenticated human acceptance of source labels, masks and candidate
specimens is still required outside tests. Automatic semantic/visibility acceptance
must remain disabled until reviewed development calibration exists. Entitlement,
invocable SKUs, finite authorized paid allowance and live smoke remain governed by
the [Phase 03 provider record](phase-03-provider-record.md); TS-AT-18 is unmet.
Phase 05 may continue against fixtures while preserving these gates. Phase 06
owns regression export; no measurement or stable finding follows from this phase.
