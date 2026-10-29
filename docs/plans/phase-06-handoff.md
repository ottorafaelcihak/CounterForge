# Phase 06 handoff — portable regressions and the MVP evidence gate

Phase 06 implements the export pipeline, standalone runner, owner finding evidence
and regression library, plus a predeclared small Spark-versus-random comparison.
The implementation is reviewable on `master`. No genuine stable model failure was
found: **live failure reproduction and a genuine regression export remain unmet**.
Fixture exports demonstrate the machinery only. The initial comparison retains an
unfinished Spark arm and makes no superiority or parity claim.

## Delivered behavior

A private export requires current sealed VALID evidence, evidence-bound independent
human review, three correct seed-baseline observations, a separate incorrect screen,
and exactly three fresh incorrect observations from the canonical completed
confirmation job. Each observation is checked against immutable invocation hashes,
exact retained image bytes, prompt/settings, settled usage and raw response records.
Export uses the confirmed candidate bytes directly and never regenerates them.

Requests bind their idempotency key to the finding and validation digest. A queued
job is claimed/recovered through the existing durable worker. CPU ZIP construction
runs outside a SQL transaction and works with providers DISABLED. Completion checks
the claim/delivery/lease fence and current eligibility again before atomically
registering the manifest, ZIP and READY reference. A changed validation revision,
revoked finding, damaged evidence or incomplete confirmation blocks download,
including direct bundle/manifest asset routes. Historical blobs remain retained.
Human showcase approval is separate from export and does not publish a demo.

`regression_exports.status` records registration state. Effective READY/REVOKED is
computed from current validity and frozen report binding on reads. Library pages
use immutable registration plus current DB validity and the small manifest; they
do not hash every large ZIP or rerun the image verifier per list row. They withhold
download links: the UI labels them Registered until an explicit Verify bundle
action passes the full detail check. Detail and download recheck full evidence and
bytes. A damaged finding's detailed evidence
becomes unavailable without breaking its whole findings list. Its summary remains
readable and its download stays blocked.

Bundles contain format `1.0.0`, manifest, instructions, original normalized PNG,
exact failing PNG, protected mask, comparison, recipe, validity report, sanitized
baseline/screen/fresh responses, static runner/test, pinned requirements and
`sha256sums.txt`. Original uploaded PNG/JPEG/WebP bytes are retained additionally
when different from the normalized image. The manifest hashes every other member
except the checksum file; the checksum file also hashes the manifest, avoiding a
recursive self-hash. ZIP ordering, timestamps and permissions are fixed. The
library publishes the independent ZIP/manifest SHA-256 anchors.

Builders reject unsafe/duplicate/symlink/encrypted/unexpected members, size excess,
missing/tampered evidence, private URLs/paths and credential patterns. Known owner
and model credentials are also checked by exact bytes, including compressed PNG
metadata and the completed static members. Unsafe evidence is refused, not silently
rewritten. Very short/common credentials can collide with legitimate content and
fail closed; use a strong distinctive owner token. No keys enter the browser or ZIP.

The standalone runner uses only Python's standard library, the product's strict
count-parser semantics, and whole-member checksum verification before invocation.
Healthy count 5 passes; wrong count 4 raises `AssertionError` with expected/actual.
That explicit comparison also works under Python `-O`/`PYTHONOPTIMIZE`. Integrity,
transport, refusal, truncation and parser problems are execution errors. Historical
replay is labeled OFFLINE RECORDED REPLAY, calls no provider and preserves the wrong
count as a failed correctness assertion. LOCAL_FIXTURE bundles cannot dispatch live.
A real-provider bundle requires explicit live opt-in and an environment key, with
one fixed-endpoint request, timeout/token bounds and no automatic retries. A
provider-managed revision may behave differently later.

Finding pages show original/candidate/diff/protected mask, validation measurements
and review history, exact target settings/model identity, raw parsed observations
by phase, denominators and usage provenance. Library/status polling, explicit failed
export retries, checksums and authenticated binary downloads are available. Fixture
labels appear in findings, exports and instructions. The BFF allows only bounded
owner routes and same-origin writes; the ZIP proxy rejects arbitrary paths, queries,
redirects and unsupported content. Polling remains appropriate to the local MVP;
large-library efficiency is later work.

## Initial matched-cap comparison

See the [pre-dispatch declaration](phase-06-comparison-declaration.json),
[full result JSON](phase-06-comparison-results.json) and
[readable comparison](phase-06-comparison-results.md). These commands are reusable:
`python -m scripts.compare_strategies declare` and `... report`; neither dispatches
a provider. Declarations reject existing reservations/plans or mismatched snapshots.
Reports revalidate settings, seed/ranges/revisions and timestamps and include zero
arms, current validity, unfinished state, per-phase calls and every cost category.

The user approved **USD 15 / 30 additional calls** for Phase 06. Both arms used the
same previously approved 480×240 five-object development seed, five exact masks,
three permitted families, `[0,1]` available strengths, target/options, independent
validity gates and 3-fresh confirmation rule. Each cap was 1 candidate, 7 target
calls, USD 7.50 and 7,200 seconds. Seed `20261030` and `random-initial-v2` are frozen;
it deterministically selects brightness strength `0.2198` for this family order.
Spark chooses strengths; random samples uniformly. This compares the P0 one-shot
strategy, not Phase 07 feedback search or the full Phase 08 ablation benchmark.

| Arm | Outcome | Baseline / screen / fresh confirmations | API calls | Confirmed USD | Unknown upper USD |
| --- | --- | --- | ---: | ---: | ---: |
| Spark ADAPTIVE | PAUSED, unfinished after image timeout | 3 / 0 / 0 | 5 | 0.006098 | 0.012000 |
| RANDOM_BASELINE | COMPLETED, correct screening count 5 | 3 / 1 / 0 | 5 | 0.005754 | 0 |
| Total | 0 suspected / 0 stable findings | 6 / 1 / 0 | 10 | 0.011852 | 0.012000 |

Spark produced a real structured plan, but image generation timed out after send.
Its possible charge is unknown, conservatively bounded at USD 0.012. No automatic
retry or replacement run hides that unfinished arm. Random needed no planner or
image-generation request. SAM detected zero usable instances on its illustration;
Otto independently approved the exact candidate hash and current report before
screening. Human approval is not SAM-mask success. The source/count/mask approval
and this candidate approval are explicit user replies, distinct from simulated
fixture decisions.

Phase 05 separately used 13 calls / USD 0.041512. The cumulative Phase 05+06 total
is **23 calls / USD 0.053364 confirmed**, plus **up to USD 0.012 unresolved**. Phase
06 had no remaining reserved/estimated charges at stop. Costs are usage-priced
ledger amounts, not a claim to have inspected the provider's final billing account.

## Retention and verification

Ignored `data/phase06-live/` retains the pre-dispatch declaration, exact approvals,
immutable artifacts/raw responses, complete graph, result, PostgreSQL custom-format
backup, asset/hash manifest and independent restore check. The backup was restored
into a separate disposable database: both report arms matched, all 25 registered
artifact hashes verified, with 10 usage events, 7 target observations and 0 findings.
The prior `data/phase05-live/` evidence remains intact. No credential values are
stored in either tracked comparison report. Task-owned database/queue resources
are cleaned up after final checks; these backups retain the live evidence.

Verification includes offline unit/contracts; PostgreSQL export/eligibility,
idempotency, recovery, fence, revocation and comparison checks; real-process fixture
browser flows; static types/lint/generated schemas/web build; and production images.
A fresh Python 3.12 environment outside the checkout with the bundle's pinned
pytest 8.4.2 independently passed healthy=0, wrong=1, replay=1 and tamper-before-
invocation=1 checks, with no repository imports, database, model key or provider
requests. `make verify-export` also verifies optimized-interpreter failure.
Final exact counts and review outcomes are recorded below after the final run.

Codex implemented the backend with `gpt-6.1-sol`; Claude Code (`claude-opus-5`)
reviewed correctness/security; Muse (`muse-wonder-1.0`) reviewed acceptance,
comparison and UI integration. The coordinator integrated UI/comparison and fixed
review findings. Optimized-Python false success and damaged-evidence list failure
were reproduced before repair. Test decisions are explicitly simulated; live human
decisions are not fabricated.

## P0 outcome and Phase 07 handoff

Portable correctness and the fixture export flow are implemented. A real Spark
plan and exact user-reviewed random screening are retained, with censored failure
and all expenses included. **A genuine stable failure, its three fresh incorrect
confirmations, and its clean live regression export have not been demonstrated.**
No automatic threshold calibration, usable live SAM masks on this illustration,
held-out benchmark, adaptive lift, feedback search or public demo is claimed.

Phase 07 can build on versioned bundles and the durable loop. Keep current validity,
accounted immutable observations, fresh confirmation and unknown-charge gates
intact. Do not resume the ambiguous Spark image request without independent
operator resolution; do not treat unused test allowance as a reason to dispatch.

### Final check record

- Offline unit/contracts: **335 passed in 6.18s**, outbound sockets blocked.
- Full integration suite: **203 passed in 223.24s** against disposable loopback
  PostgreSQL. After the final no-link library change, all **8 affected export
  cases passed in 26.37s** again.
- Final fixture browser suite: **7 passed in 57.66s**, including source/mask review,
  candidate audit, experiment controls, verify-before-download, revocation,
  authenticated/CSRF/binary routing, mobile layout and explicit failed-export retry.
- Mypy: **90 source files**, clean. Ruff lint/format: clean, **166 files**.
  Web ESLint, TypeScript/production build, generated OpenAPI/TS drift, Compose
  configuration and production API/worker/web image builds passed.
- Network-disabled/read-only production-container imports verify the installed
  regression templates, API, worker and comparison modules. Supplied SQL and the
  initial migration remain byte-identical. Exact configured credentials were
  absent from all 46 changed/new tracked candidate files; no values were printed.
- `make verify-export`: healthy=0, broken=1, replay=1, tamper=1, optimized=1.
  Separate exact-pinned pytest 8.4.2 environment verification passed as above.
- Claude's follow-up resolved/accepted the previous HIGH/MEDIUM findings and
  confirmed no download bypass. Muse's closing review: **no material issues
  remaining**, including the lightweight-list Registered/full-verification gate.

All live provider requests are excluded from these offline/fixture test counts.
The final direct-master commit also preserves the requested Meta hackathon README
introduction. Phase 06 is committed locally; pushing requires a separate request.
