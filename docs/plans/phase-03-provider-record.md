# Phase 03 provider integration record

Checked: **2026-10-28**. Documentation facts below were verified by the coordinator against primary Meta documentation on that date; installed package APIs/source and offline fixtures were inspected by the package implementation worker. Construction of an SDK client does **not** verify entitlement or accessible models.

## Primary sources and selected interfaces

| Capability | Selected standard SKU / endpoint | Source |
| --- | --- | --- |
| Planner and authorized counting target | `muse-spark-1.3`, Chat Completions `/v1/chat/completions`; planner uses `response_format.json_schema`, strict root object, all properties required and `additionalProperties: false` | [Structured output](https://dev.meta.ai/docs/structured-output) |
| Scene editing | `muse-image-1.0`, multipart `/v1/images/edits`; `n=1`, `response_format=b64_json`, `output_format=png` | [Image generation](https://dev.meta.ai/docs/image-generation) |
| Image segmentation | `sam-3.1`, streaming `/v1/responses` with text concept and image data URL | [Media segmentation](https://dev.meta.ai/docs/media-segmentation) ; [SAM client libraries](https://dev.meta.ai/docs/sam/client-libraries) ; [SAM cookbook](https://dev.meta.ai/docs/cookbook/sam-api-basics) |
| Prices and limits | Standard Spark: input $1.25/M, cached input $0.15/M, output $4.25/M; context 1,048,576. Image: $0.01 per returned image, zero for no returned/filtered images; token usage informational. SAM: $0.0025 per input image | [Meta Model API prices](https://dev.meta.ai/products/meta-model-api) |
| Retry policy | Documented 429 rejection permits bounded retry with jitter and at least Retry-After; exhausted quota is not retried. The application deliberately does not retry ambiguous 500/503 errors, even though provider guidance suggests retries | [Pricing and rate limits](https://dev.meta.ai/docs/pricing-rate-limits) ; [Error handling](https://dev.meta.ai/docs/error-handling) |

The fixed API origin is `https://api.meta.ai/v1`. There is no automatic contributor-SKU substitution. A different returned model SKU is invalid and retains an UNKNOWN charge. No official generated-image CDN host was verified: the production URL allowlist is **empty**. Base64 is always requested. Exact trusted hosts and a resolver/MockTransport can be injected for fixture tests.

Installed and inspected: `openai 2.54.0`, `httpx 0.28.1`, `meta-sam-parser 0.1.0`, `Pillow 13.0.0`. Coordinator also installed `Playwright 1.64.1` for later UI work. The parser distribution metadata identifies **SAM License**, last updated November 19, 2025 (not Apache/MIT). Its complete license is distributed with the installed package; redistribution must retain the applicable license materials.

## Accounting and package interfaces

`PriceBook.load(explicit_path)` loads the central root `pricing.toml`. Its revision includes the file digest. Parent deployment must copy this file into its image and inject the path; packages do not search working directories or settings. The Spark reservation upper bound charges the **entire input context cap** plus the requested completion cap (maximum 4096). No guessed vision-token count is used. At the default 512 completion cap this is $1.312896. Image upper bound is $0.012 ($0.01 + $0.002 margin), SAM $0.003 ($0.0025 + $0.0005 margin). Planner advisory estimates also read the injected price book.

The package has no API-service, repository, database or settings imports. All adapters receive a transport whose accounting port is injected:

```python
async Accounting.reserve(intent: CallIntent) -> Ticket
async Ticket.claim() -> bool
async Ticket.finish(outcome, charge, metadata, sanitized_raw) -> ProviderResult
```

`CallIntent` is frozen and contains `model`, `category`, `semantic_sha256`, `pricing_revision`, `upper_bound_usd: Decimal`, `attempt: int`. `Charge` is frozen and contains `kind` (`ACTUAL`, `ESTIMATED`, `UNBILLED`, `UNKNOWN`), `amount_usd: Decimal`, `request_sent: bool`, and a fixed `reason`. No absent usage is priced at zero. Spark actual cost requires trustworthy nonnegative integer usage, bounded cached tokens and output within the requested cap; otherwise it is ESTIMATED. Image cost uses completed returned-image count, never token usage. SAM completed input-image cost uses its fixed tariff. EOF without a terminal SAM event is locally INCOMPLETE with an UNKNOWN upper-bound charge, never an ordinary settled estimate. Ambiguous dispatched operations retain UNKNOWN upper-bound charges.

The parent must commit reservations, enforce global job/call/dollar caps and semantic idempotency conflicts, commit a successful claim **before wire I/O**, and persist/commit finalization. A false claim returns NOT_DISPATCHED and does not finalize a ticket owned by another claimant. Port exceptions propagate; they stop further provider attempts. A failed finalization remains a claimed durable reservation for the parent to quarantine/recover. Cancellation after dispatch shields finalization and re-raises cancellation. Each safe retry gets a new attempt ticket only after its predecessor is finalized UNBILLED. Attempts are deterministic 1–3 under one absolute wire deadline. Proven connection-establishment failures are safe; 429 retries additionally require `unbilled_429=True` at the server factory. Read/write/protocol timeouts, ambiguous 5xx and unknown outcomes never retry.

`MetaTransport(api_key=..., accounting=..., prices=..., http_transport=...)` exposes `chat`, `image`, `sam`, async context management and `aclose`. All HTTP/SDK retries are zero. The parent owns provider concurrency (default one). `CallResponse` exposes `outcome`, `metadata: ProviderResult`, and internal `data`/`events` fields with `repr=False`. The package itself does not own public DTOs; the backend integration now exposes typed setup, job results/usage and mask-review DTOs.

- `SparkPlanner(transport=...).plan(contract, history, remaining_budget)` returns one independently VTC-validated MutationPlan; invalid/refused/incomplete proposals yield a modest explicitly labeled deterministic fallback. `last_evidence` retains sanitized raw proposal, hash, template version and author/fallback reason. Planner context excludes reviewer names and mask UUIDs.
- `SceneGenerator(transport=..., url_policy=..., download_transport=...).create_scene(...)` returns validated bounded image bytes. URL downloads require exact trusted hostname, HTTPS/443, no userinfo/IP literals/private or non-global DNS; each redirect is revalidated. Connections pin a validated public IP while retaining the host and TLS SNI. Downloads carry no provider auth, have a 10 MiB cap and do not persist remote URLs.
- `MetaSparkTarget(transport=..., expected_count=...).evaluate(image_bytes, prompt, pinned)` uses the existing strict `parse_count`. Refusal/cutoff/ambiguous text cannot assert a count. Effective `temperature`, `top_p` and token cap are validated, sent and recorded; `max_output_tokens` maps to the Chat Completions token cap, conflicting aliases/unsupported options are rejected. `version_claim` is honestly `provider-managed`.
- `normalize_events(events, width=..., height=...)` returns pure `NormalizedSegmentation`; `SamSegmenter(transport=..., mask_writer=...)` implements the existing Segmenter protocol. The async writer signature is `(object_id: str, png_bytes: bytes) -> UUID`; the parent stores immutable mask assets. Full validation finishes before any writer is called. No human expected count is changed by this package.

## Wire, raw evidence and SAM isolation

The HTTP transport bounds **raw streamed bytes before SDK eager decoding**: aggregate image response 16 MiB, text 1 MiB, SAM SSE 4 MiB, errors 64 KiB. Requests use identity encoding; unexpected compression is rejected. Finite connect/read/write/pool timeouts and the absolute call deadline bound wire activity. Outbound host/scheme/port are fixed. An outbound header allowlist reconstructs auth from the injected key and drops arbitrary SDK/environment headers. Explicit empty SDK org/project/admin/webhook options suppress their environment fallbacks. OpenAI 2.54.0 nevertheless reads `OPENAI_CUSTOM_HEADERS` internally: supported SDK construction cannot disable that read. The tested wire allowlist prevents forwarding those values; the application neither reads nor mutates global environment configuration itself. HTTP clients use `trust_env=False`.

Finalization receives recursively allowlisted raw JSON, redacting secret patterns, URLs and long base64 strings; headers, media and exception bodies are never passed. Raw evidence includes prompt-template and pricing revisions. Provider request IDs are bounded and sanitized; SAM uses the HTTP x-request-id, not the response object's unrelated ID. Raw media/events are hidden from object reprs.

The official parser eagerly decodes mask payloads before caller quotas. Parsing therefore runs in a Linux subprocess with **512 MiB address space, 5 seconds CPU, 10 seconds wall time**, bounded 4 MiB input and 16 MiB output, empty inherited environment, discarded stderr and no application credentials. It exclusively calls public `parse_responses_stream(async_events, image_segmentation_format()).final_result()` and `decode_mask_to_raster`; there is no custom SAM grammar or JSON decoding of segmentation text. It rejects completed results with ERROR diagnostics, EOF without terminal completion, empty completed results, duplicate/missing IDs, video frames, more than 16 objects, invalid/out-of-source boxes, mismatched box-local mask dimensions, and empty/nonbinary rasters. Official inclusive wire boxes become half-open through the parser. Masks are pasted into new binary full-source-size PNGs, never resized to manufacture matching dimensions. Invalid output persists no masks and returns fixed diagnostics.

Wire logging policy: adapter factories disable named OpenAI/httpx/httpcore wire loggers. Regressions verify that DEBUG request media/model keys and INFO signed download URLs do not reach captured logs.

## Durable backend integration (2026-10-28)


`Settings` defaults to `PROVIDER_MODE=DISABLED`, `ENQUEUE_JOBS=0`, one concurrent provider call and a global pending-job ceiling of 100. Fixture mode requires `APP_ENV=development|test` plus `ALLOW_SYNTHETIC_FIXTURES=1`; live mode requires `RUN_META_LIVE_TESTS=1` and a nonempty server-only `SecretStr` model key. Every provider dispatch additionally requires explicit finite `JobLimits` and a separately configured, unexpired operator deployment allowance. No fixture fallback exists. Owner-only `GET /api/v1/setup` exposes safe capabilities; `GET /api/v1/fixtures/development-seed` exists only in fixture mode.

`services/api/app/provider_accounting.py` implements the async package port with short, committed UOWs. Keys are `jobUUID:delivery:purpose:attempt`; semantic and pricing identity conflicts fail closed. Claims validate owner, job token, delivery, lease, state and runtime before committing the dispatch. A dedicated PostgreSQL **session** advisory lease enforces global provider concurrency one across workers and smoke; its transaction is committed before I/O. A busy claim voids the undispatched reservation and refunds its allowance. Finish stores an immutable sanitized RAW_RESPONSE artifact and pricing, semantic, outcome, attempt and exact finish fingerprint metadata. Changed finish replays conflict. ACTUAL settles actual usage; ESTIMATED leaves actual NULL and consumes the conservative upper bound; UNKNOWN quarantines and retains that upper bound. Trusted UNBILLED proofs refund reserved dollars once; pre-send proofs also refund call capacity, while a sent unbilled 429 still consumes one call. Invalid proof/sent pairs cannot create allowance. Existing ACTUAL/ESTIMATED/UNKNOWN shared allowance policy stays conservative.

`BackgroundJobs` records newly inserted IDs and checks existing idempotency keys before capacity under one serialized global advisory lock. HTTP notification happens after the database commit. Redis failure leaves durable QUEUED intent. RQ uses JSONSerializer with a canonical UUID-only argument, unique delivery notification IDs and no automatic retry. The worker handles only SEGMENT and TARGET_CHECK. Startup and `make recover-jobs` rescan queued jobs and expired claims. Claims use an atomic token/delivery increment, 30-second lease and 5-second heartbeat. Duplicate deliveries do nothing; stale completions cannot overwrite a new delivery. Undispatched expired reservations are voided and requeued; any dispatched unresolved attempt becomes UNKNOWN/REVIEW_REQUIRED, and settled-but-lost output requires a manual new job. Bounded mask decoding runs in a thread so the heartbeat can continue.

Mask proposals are binary-decoded before persistence, and all proposal artifacts survive a late human edit. Completion holds the job fence and calls `MaskReview.apply_proposals` with the queued mask generation. Changed drafts retain their human edits and receive REVIEW_REQUIRED diagnostics. SAM count mismatch never changes the human expected count or auto-approves masks. Shrink/expand retries retain distinct append-only retired revisions and exact prior approvals.

The original CC0 development seed is `fixtures/phase03/five-blue-containers.png`, 480x240, with five disjoint blue rectangles by construction. `manifest.json` records source, rights, exact SHA and half-open rectangle coordinates. Fixture dispatch supports only its known normalized SHA and uses SDK `httpx.MockTransport`, never an endpoint. SAM's four full-rectangle records use the official public parser and codec payload `!!!!!(y:Whj]K7`; Spark target returns 5. All fixture provider usage is labeled FIXTURE/synthetic with ACTUAL $0, finite job limits and an explicit zero-dollar deployment allowance. A four-capability fixture smoke also exercises planner, SAM, image and target through the same durable accounting and leaves the live gate **UNMET**.

`make smoke-meta` / `scripts/smoke_meta.sh` are fail-closed entry points. Live CLI requires `PROVIDER_MODE=LIVE`, `RUN_META_LIVE_TESTS=1`, explicit `SMOKE_META_MAX_USD`, `SMOKE_META_MAX_CALLS` (at least 4), `SMOKE_META_MAX_RUNTIME_SECONDS`, and existing owned `SMOKE_META_SEED_ID`, `SMOKE_META_VTC_ID`, `SMOKE_META_TARGET_ID`. It verifies current reviewed contract evidence and an existing operator allowance before dispatching planner, SAM, image and target sequentially. All four use the real Accounting and global provider lease; sanitized evidence IDs and usage remain durable. Neither command was invoked live. Spark now requires the exact echoed model before interpreting output; missing/wrong Spark model is INVALID with UNKNOWN charge. SAM/image may omit model in their official response schemas.

Setup-job runtime also bounds parsing and mask decoding through an outer deadline calculated from the committed job start and database clock. Transport deadlines cannot exceed the remaining runtime. Timeout produces a reviewed failure, retains truthful billing and never applies partial drafts. Heartbeat cleanup cannot leak a driver exception or mask an already committed result. Failed advisory unlock invalidates the physical connection so a session lock cannot return to the pool. Cleanup scans only the current delivery. Late billing cannot reverse a quarantined UNKNOWN charge; the Budget finalizers reject that transition.

## Owner workspace and deployment

The Next app provides project/seed/target setup and a source-sized binary mask editor with continuous paint/erase strokes, bounded undo, rectangle controls, overlays, revision history, extra-mask retirement and discrepancy acknowledgement. No-op edits do not create revisions or clear approvals. Unsaved work has navigation/sign-out guards and survives session renewal. Collection reads follow scoped cursors under finite page/time limits.

The server-only HMAC session uses an opaque nonce, eight-hour expiry, HttpOnly and SameSite=Strict (Secure for HTTPS). Every BFF route authenticates; writes require exact configured Origin. Proxy destinations and paths are allowlisted, redirects rejected, request/response bodies bounded, and response types constrained. Browser state contains no configured model key or persisted owner token.

API/worker Docker images copy root pricing and the original development fixture. The non-root standalone web image receives only the owner token and internal API URL, with no model key. Compose exposes loopback ports and fails closed by default. CI provisions disposable PostgreSQL/Redis and runs the fixture-backed Playwright flow.

## Verification and outstanding gates

See the [phase 03 handoff](phase-03-handoff.md) for final commands, independently inspected evidence, review rulings and team participation. Default tests block provider sockets. The real-process browser test uses its own schema, Redis queue, artifact root and zero-dollar fixture allowance, then removes those owned resources.

**UNMET:** live entitlement/credit, accessible/invocable SKU verification, authorized real-provider smoke and TS-AT-18. No model catalog was implemented; curated IDs are configuration choices, not access claims. No live Meta API call was dispatched. An automated fixture approval is test evidence, not a production human sign-off.


## Phase 05 live verification update — 2026-10-29

The [Phase 05 handoff](phase-05-handoff.md) records later authorized real Meta
requests: Spark baseline/planning/screening, Muse Image generation and SAM
segmentation. Total 13 application calls cost USD 0.041512 from retained usage and
published standard prices. The reviewed candidate returned the correct count 5;
no real model weakness is claimed. The first planner truncated at 1024 hidden
reasoning/output tokens; the revised bounded request selects low reasoning and
4096 output tokens. Each run retains its actual template, response and outcome.

Real access is now demonstrated. SAM returned zero instance proposals for this
illustration, preserved as INCOMPLETE supporting evidence and resolved only by
explicit independent human semantics/visibility review. This is a documented
TS-AT-18 deviation for usable segmentation quality, not proof of successful
instance detection. The source count/masks and accepted candidate have explicit
user approval; the Phase 04 calibration specimens retain their separate pending
actual-human/calibration status. No Phase 03 historical result is rewritten.
