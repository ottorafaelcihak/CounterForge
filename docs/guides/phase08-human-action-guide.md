# Phase 08: your steps for a genuine benchmark

Prepared November 2, 2026. **The real benchmark is not complete.** No Phase 08
spending is authorized. The work below prepares a supervised run; it does not
approve labels, rights, billing, publication or submission.

## One-page quick-start

**AUTOMATED BY CODEX:** inspected the existing evaluation harness and manifest
templates, checked the historical proposal location, prepared twelve blank review
slots and a matched protocol draft, and arranged a no-money rehearsal. See the
[status ledger](../benchmark/00-status.md) for actual results and evidence paths.
The old `/tmp/counterforge-phase08-dataset` is absent in this host's test container.
No earlier proposal dataset is available to reuse here.

**ACTION REQUIRED FROM USER:** complete the following in order. Each unchecked
item is a real gate. Send one consolidated response with the image directory,
completed review records, chosen target/settings, proposed spending limits and
reviewer availability. Do not include an API key. Choosing files or discussing a
budget does not authorize spending; authorization is a later, separate decision.

| Check | Gate and current handoff |
| --- | --- |
| [ ] | Supply 12 distinct files: `COMPLETE` (technical preparation only) |
| [ ] | Independently count each image, resolve ambiguity, approve redistribution: `WAITING_FOR_USER` |
| [ ] | Confirm 8 development and 4 held-out cases, without outcome-based selection: `WAITING_FOR_USER` |
| [ ] | Codex normalizes, hashes, validates and freezes only completed reviews: `BLOCKED` |
| [ ] | Choose eligible target/settings; review call, dollar, runtime and expiry limits: `WAITING_FOR_USER` |
| [ ] | Codex creates reviewed contract bindings and freezes the protocol: `BLOCKED` |
| [ ] | Separately authorize a new Phase 08 paid run and enter the key locally: `BLOCKED` |
| [ ] | Supervise candidate reviews during the bounded run: `BLOCKED` |
| [ ] | Independent person completes the blinded audit: `BLOCKED` |
| [ ] | Codex verifies the source/report bundle and qualifies conclusions: `BLOCKED` |
| [ ] | Separately approve publication and submission: `WAITING_FOR_USER` |

Return completed records to Codex with `READY_FOR_CODEX`. Codex checks actual
files and receipts before marking a gate `COMPLETE`. Never check a box merely
because a command is available.

## 1. Choose and review the twelve files

Work in local files. The private working directory on this Windows host is
`C:\Users\ottoc\Projects\CounterForge\.cache\phase08\private`; its
`assets` directory contains private source and normalized copies of the twelve
files you supplied in `docs/images`, not an approved dataset.
Nothing in that directory is committed. The tracked
[review worksheet](../benchmark/drafts/image-review.md) contains twelve blank
slots. The [manifest draft](../benchmark/drafts/dataset.json) contains computed
source/normalized hashes and dimensions, but no approved counts or rights.
All twelve source and normalized hashes are distinct. Proposed splits still need
your confirmation. Open `.cache/phase08/private/review/index.html` locally for a
printable per-image packet; it links full-resolution normalized copies. The static
packet does not save entered text: print it or complete the worksheet. Original
files in `docs/images` were preserved and are not included in this documentation commit.

1. Choose 12 original or licensed JPEG, PNG or single-frame WebP files. Use clearly
   countable objects, 1–16 per image, with varied counts, sizes and backgrounds.
   Existing synthetic repository fixtures are software tests, not twelve reviewed
   benchmark cases. Do not substitute them automatically.
2. An independent human opens each image and writes the counting question,
   observed count, handling of partially visible objects and ambiguity rationale
   in the worksheet. Do not derive the label from generator metadata or model
   answers. If counting is unclear, exclude that case with a reason and choose
   a replacement; do not mark it clear to satisfy the validator.
3. For every file, record its source URL or original author, exact license or
   permission, evidence location, and whether redistribution is permitted. Keep
   the actual license/permission record privately. Owning a downloaded copy does
   not establish redistribution permission. Codex cannot approve this for you.
4. Record the real label reviewer and rights reviewer, actual review times with
   timezone (for example `2026-11-02T20:00:00+01:00`, only if true), and notes.
   Choose 8 `development` and 4 `heldout` slots before seeing benchmark outcomes.
   Near duplicates and the same scene across splits need human inspection even
   when byte hashes differ. Held-out cases are for final reporting, never tuning.

Keep rejected case IDs, source hashes and reasons in `exclusions`. A replacement
gets a distinct ID. Hand off the image directory and worksheet; Codex will copy
the exact files, create normalized bytes using `normalize_image`, fill technical
hashes/dimensions and transcribe only your completed attestations. Human count
and reviewer must later agree with the imported seed. A contact sheet alone is
insufficient: inspect the originals at useful resolution.

## 2. Validate, fix errors and freeze the dataset

**BLOCKED UNTIL APPROVED:** Codex performs this after your real reviews. Commands
below are Linux/Ubuntu commands from the repository root, with a synchronized
Python 3.12 environment (`uv sync --locked`). On this Windows host use the prepared
Linux Docker runtime; do not run the POSIX evaluation runner in native Windows.
`<...>` means replace with a real path or ID; angle brackets are not literal input.
Use dedicated private paths that contain no `.env` components and do not end in
`-live`. See `.cache/SETUP.md` for the current Docker runtime.

```bash
.venv/bin/python -m evaluations.cli freeze-dataset <REVIEWED_DRAFT.json> \
  --assets <ASSET_ROOT> --output <NEW_FROZEN_DATASET.json>
.venv/bin/python -m evaluations.cli validate-dataset <NEW_FROZEN_DATASET.json> \
  --assets <ASSET_ROOT>
```

These command signatures are checked with `--help`. A successful validation
prints `Dataset hashes, independent human evidence and 8/4 membership verified.`
Freeze succeeds silently and writes canonical JSON with its `sha256` field.
Save the frozen JSON, exact original and normalized images, reviewer records and
terminal transcript. Codex revalidates the bytes and split, not just the filename.

Remove template-only `_status` fields from working JSON: models forbid extra
fields. Leave no `PENDING`, null human fields or made-up timestamps. Do not feed an
unsealed draft to `validate-dataset`: that command checks the existing seal.
`freeze-dataset` validates and seals reviewed inputs; it grants no human approval.
Wrong hashes require checking the actual source/normalization output. Do not
hand-edit frozen hashes. For corrections use a new revision/output path and
preserve the previous evidence. Duplicate images, unclear labels or missing
rights mean stop and fix the underlying case.

## 3. Select the target and approve a proposed envelope

Work with Codex in the owner web app at `http://localhost:3000`; use the owner
token from your local `.env`, not chat. Select or create your project, import the
reviewed seeds and approve exact source masks. Creating segmentation proposals
through a provider can cost money: this preparation does not authorize it.
Manually reviewed masks can be prepared without paid proposals.

After the worksheet is genuinely complete, follow these existing screens:

1. Open **Projects**, choose **New project**, enter a project name and create it.
   Retain the project ID from `/projects/<PROJECT_ID>`.
2. Choose **Add source seed**. Under **Add a source seed**, select **Source image**,
   set **Counting question**, replace the default **Human count** with the actual
   reviewed count, and enter the actual **Label reviewer** from the worksheet.
   Check the rights/count acknowledgement only when true, then **Upload seed**.
   Do not leave the example blue-container question/count for unrelated images.
3. Choose **Review masks** for that seed. Manually use **Add missing instance**
   and the mask editing controls to delineate each protected object, save each
   correction with real notes, inspect it, and **Approve this revision** for
   every object. Approve exactly one distinct mask per counted instance. If the
   screen requests a count discrepancy acknowledgement, inspect and resolve it
   explicitly; never let the proposal count replace the human count. Do not
   request provider segmentation without separate paid permission.
4. Choose **Create visual test contract** only after all masks are approved.
   Codex checks the two contracts' family ordering and reproduction settings below
   against the frozen policy before using their IDs in bindings.
5. In **Target setup**, inspect **Configure a target**, **Target model** and
   **Target instruction**, then **Save target** after choosing approved settings.
   **Check target** may spend money and remains outside this preparation approval.

Choose a model you are entitled to test and record model ID/revision label,
prompt and inference options. Do not assume a target check or provider handshake
is free. The adapter freezes the fingerprint of `adapter_key`, `provider`,
`model_id`, `model_revision`, `prompt_template`, `inference_options`; revision
labels do not pin provider weights. Codex computes the fingerprint from actual
saved settings and records target/project IDs.

Each case needs `contract_id`, `full_contract_id`, `target_config_id` in the
[bindings draft](../benchmark/drafts/bindings.json). Codex creates two reviewed
contracts for the same seed. Their allowed-family **order matters**:

- Primary: `brightness_shift` only.
- Full-system ablation: `brightness_shift`, `background_replacement`,
  `background_clutter`, in precisely that order.

In the contract UI, clear all mutation-family selections before selecting them in
that order, or let Codex prepare the exact existing API payload. Both contracts
need 3 attempts/3 required fresh failures and current approved masks. The full
contract is required; a null binding is not a usable fallback.

The [protocol draft](../benchmark/drafts/protocol.json) proposes 10 total target
calls and 3 candidate attempts per arm per seed, seed 17, 600 global provider calls,
USD 5 global exposure and 3600 seconds. **These are unapproved planning caps, not
a price quote or permission.** Twelve cases × three arms × ten calls = at most
360 target calls (240 primary, 120 ablation), including 108 unchanged-seed baseline
calls. Fresh confirmations consume this same allowance. Planner/Image/SAM calls
also consume the global API/dollar/runtime limits. A small cap may censor a run.

Codex must reconcile current entitlement, pricing and worst-case per-request
inputs/outputs before recommending a dollar estimate. If a request's maximum
cost is bounded by R USD, the conservative aggregate estimate is `600 × R`;
R is currently unverified, so no complete-run dollar estimate is asserted. The
hard USD 5 exposure proposal does not prove the campaign can finish for USD 5.
You choose call/dollar/runtime caps and an explicit timezone-aware expiry after
reviewing that worksheet. Free credits do not authorize arbitrary spending.

## 4. Freeze the protocol, then separately authorize spending

After dataset/target approval, Codex fills the actual dataset hash, target
fingerprint and code revision, removes `_status`, and uses:

```bash
.venv/bin/python -m evaluations.cli freeze-protocol <REVIEWED_PROTOCOL.json> \
  --output <NEW_FROZEN_PROTOCOL.json>
```

Preserve the frozen JSON/hash and approved envelope. The primary comparison is
`random_det` versus `spark_det`, both `brightness_shift`, range `[0,1]`, same
seeds/settings/caps. `full_system` stays a separately reported ablation.
`FEEDBACK_V1` maps adaptive arms to feedback and random to seeded `RANDOM_V1`;
`ONE_SHOT` cannot demonstrate feedback lift. Order reverses on odd seed ordinals.
Freeze before any paid calls; never change policy based on held-out outcomes.

Now make a **separate explicit Phase 08 authorization** naming both frozen
hashes, your identity, actual authorization time, expiry, maximum provider calls,
dollars and runtime, and explicit permission for paid provider calls. A Phase 07
allowance is not transferable. Codex checks an existing finite operator allowance
and prepares a private sealed `LiveAuthorization` only after this decision. Its
fields are shown in `evaluations/manifests/live-authorization.template.json`;
remove `_status`, never commit the real file, and do not fabricate authorization.
The seal is an integrity checksum, not proof of human consent or a digital signature.

Do not enable LIVE mode on the ordinary background worker. The evaluation CLI
uses serial deliveries with queue dispatch disabled. **Before an authorized run,
stop ordinary workers sharing that database/queue and configure the review API
with `ENQUEUE_JOBS=0`**, or use a fully isolated benchmark deployment. Otherwise
the review UI's Resume can queue LIVE evaluation work to a DISABLED ordinary
worker, which pauses it with `PROVIDER_MODE_MISMATCH`. Only the evaluation CLI
may dispatch this campaign. Do not fix that mismatch by enabling LIVE globally.

For the current local stack, Codex will prepare an ignored review override:

```yaml
services:
  api:
    environment:
      ENQUEUE_JOBS: "0"
```

At the authorized preflight, save it as `.cache/phase08/compose.review.yaml`,
then use the existing Compose commands (not executed as part of this preparation):

```bash
docker compose -f compose.yaml -f .cache/compose.setup.yaml stop worker
docker compose -f compose.yaml -f .cache/compose.setup.yaml \
  -f .cache/phase08/compose.review.yaml up -d --no-build api
```

Codex must verify no other workers dispatch against the same database and that
the review API actually has queueing disabled. Keep providers disabled in the
ordinary stack. Worker-heartbeat readiness may report false while that ordinary
worker is intentionally stopped; do not re-enable it to satisfy the check. After
all evaluation work is terminal and reconciled, remove the review override from
the Compose command and restart the normal disabled-provider stack. Budget,
runtime and expiry cover review waits too.

## 5. Enter a key securely and start only the authorized run

The existing CLI reads one line from standard input with `--key-stdin`; it has no
masked prompt itself. Use a trusted local terminal, not a recorded session, and
keep secrets out of shell arguments/history, Markdown and chat. Codex will
prepare the following wrapper with real approved non-secret argument values.
The command shape was checked through `run --help`; **no live invocation was run**.

```python
# Save locally as a private launcher only AFTER authorization; run with Python.
import getpass, subprocess
args = [".venv/bin/python", "-m", "evaluations.cli", "run",
        "--dataset", "<FROZEN_DATASET.json>", "--assets", "<ASSET_ROOT>",
        "--protocol", "<FROZEN_PROTOCOL.json>", "--bindings", "<BINDINGS.json>",
        "--project-id", "<PROJECT_ID>",
        "--database-url", "<LOCAL_DATABASE_URL>",
        "--artifact-root", "<SHARED_SEED_AND_REVIEW_ARTIFACT_ROOT>",
        "--output", "<DURABLE_RUN_DIRECTORY>", "--live",
        "--live-authorization", "<PRIVATE_AUTHORIZATION.json>", "--key-stdin"]
# URL must omit an embedded password; a separate masked prompt can supply PGPASSWORD.
import os
env = dict(os.environ)
env["PGPASSWORD"] = getpass.getpass("Local database password: ")
key = getpass.getpass("Authorized Meta API key: ")
try:
    subprocess.run(args, input=(key + "\n").encode("utf-8"), env=env, check=True)
finally:
    del key
    env.pop("PGPASSWORD", None)
```

Use a PostgreSQL URL such as `postgresql+psycopg://counterforge@127.0.0.1:5432/counterforge`
in the actual deployment network; credentials stay in memory. Never pipe the
key through `echo`, store it in the launcher, or pass it as a CLI argument.

Before launch, Codex must verify the runner and review API use the **same database,
project/owner namespace and backing artifact store**. The runner root must resolve
the imported seeds and masks, and the review API must read new candidates from
that same backing store. Do not choose a new empty artifact directory for seeds
already imported into the UI. Container path spellings can differ while sharing
the same volume. On this host that is the `counterforge_artifacts` volume, mounted
at `/data/artifacts` in the API; mount the same volume into the runner. Its Linux
PostgreSQL network address also differs from the host's loopback example. Verify
source/mask hashes and API image reads before spending. The CLI constructs its
own settings; confirm its owner scope matches the imported project rather than
assuming arbitrary host environment settings are loaded.

Save durable `state.json`, experiment IDs, snapshot bundles and usage receipts.
An exit code of zero can mean `WAITING_REVIEW`, not campaign completion.
Codex must inspect state, unresolved charges, terminal arms and snapshots.

## 6. Review pauses and recover safely

In the owner app open `/experiments/<EXPERIMENT_ID>` from saved run IDs and each
candidate `/mutations/<MUTATION_ID>`. Under **Record a human review**, inspect the
source/candidate, masks and diff; choose **Semantic integrity**, **Visibility**,
write **Review notes**, then **Record review**. Record what you actually observe,
including extra/missing objects, clipping and ambiguity. Structural failures
cannot be approved away. Reload stale evidence; do not change the expected label.

After valid review, inspect the experiment's saved stop reason and choose
**Resume** only within the still-valid authorization. Coordinate this with Codex:
the evaluation process must be rerun using the same immutable files and durable
output directory; do not create a second run or click Start experiment again.
Pause/cancel may drain charged in-flight work. Unknown outcomes/charges require
operator reconciliation before retries. Expired authorization or a global cap
means stop; no silent extension, new output directory or quota increase.
Keep before/after state snapshots and real review identities/times.

## 7. Complete an independent blinded audit

Codex exports candidate images as `<CANDIDATE_ID>.<ext>`, verifies their exact
source hashes and runs:

```bash
.venv/bin/python -m evaluations.cli audit-export <SOURCE.json> --seed 17 \
  --output <PRIVATE_AUDIT_SELECTION_DIR> --images <VERIFIED_CANDIDATE_IMAGES> \
  --packet-dir <NEW_REVIEWER_PACKET_DIR> --private-mapping <PRIVATE_MAPPING.json>
```

Packet, selection and mapping directories must be separate. Give the independent
reviewer **only the packet directory**, never strategy mapping, population or
selection files. The CLI packet is candidate-only: it includes the question,
expected count, hash-bound images and `review-form.json`. Source-comparison media
requires Codex's existing lower-level packet API with verified comparison hashes;
do not claim candidate-only evidence proves an unavailable comparison.

The reviewer independently observes the count, records `PASS`, `FAIL` or
`AMBIGUOUS`, rationale, real identity and actual timezone-aware review time for
each requested candidate. Review every headline stable/showcase finding plus
the separate reproducible stratified random accepted/rejected sample (up to five
each per family/strategy). No available sample or zero findings is reported as
such; do not invent audit rows. Missing media or insufficient context means hold.

Keep completed raw forms outside the immutable packet. Codex verifies the packet,
then transcribes genuine decisions into `AuditDecision`: exact candidate/image/
evidence hashes and original/automatic verdicts, independent reviewer, time,
judgment, rationale and `supersedes`. The raw observed count goes in the retained
form and rationale, not an extra model field. New decisions use 64 zeroes for
`sha256` (not null); `audit-append` computes the seal:

```bash
.venv/bin/python -m evaluations.cli audit-append <SOURCE.json> \
  <PRIVATE_DECISIONS.json> <COMPLETED_DECISION.json>
```

Corrections append and set `supersedes` to the previous decision SHA. Never
rewrite audit history or reuse the candidate-review identity as independent audit.

## 8. Verify results, then separately decide about publication

Codex runs, using the actual frozen random sample and completed decisions:

```bash
.venv/bin/python -m evaluations.cli report <SOURCE.json> \
  --sample <PRIVATE_AUDIT_SELECTION_DIR>/selection.json \
  --decisions <PRIVATE_DECISIONS.json> --output <NEW_RESULT_BUNDLE>
.venv/bin/python -m evaluations.cli verify-bundle <NEW_RESULT_BUNDLE>
```

Expected verification: `Bundle file hashes and report-to-source reconciliation verified.`
Keep source JSON, report JSON/CSV, manifest, dataset/protocol hashes, run IDs,
receipts and private audit chain. Verification proves reconciliation, not rights
or the truth of a human attestation. Inspect `primary_comparison_valid` and any
withheld comparison reasons. Missing arms/global censoring prohibit a lift claim;
zero random denominator withholds a ratio. Null rates stay null. Report actual,
estimated and unknown spend separately. Zero real confirmed failures is valid;
the methodology fixture's invented findings are never real-provider results.

Review the resulting claims and proposed public media with Codex. Approving a
dataset or paid run does not approve release. Publication, redistribution of
specific media and organizer submission each need their separate explicit
approval. No deadline or organizer eligibility verification is claimed by this guide.

## Troubleshooting and safe stopping

- `Evaluation blocked`: retain the message and inputs, fix the real missing gate.
  Do not loosen validation or manually replace a stored seal.
- Immutable output exists: preserve it; prepare a new revision for changed
  dataset/protocol/report artifacts. Resume a run only in its original directory.
- Native Windows `fcntl`/resource errors: use Linux/Ubuntu or the prepared Linux
  container. Preserve LF endings (`core.autocrlf=false` locally).
- No model access, exhausted credits or expired credentials: stop and verify
  entitlement privately. Claiming credits is a separate provider-account action;
  this guide does not invent billing-portal screens or grant a budget.
- Paused too long, unknown charges or missing receipts: stop. Codex reconciles
  saved state; any new paid authorization is a new explicit human decision.
- Packet extra/missing files or hash mismatch: rebuild from verified source into
  a new revision; keep completed forms outside the packet. Never rename arbitrary
  images to satisfy candidate IDs.
- No independent reviewer or rights approval: benchmark remains `BLOCKED`.
  Deliver the prepared workflow and truthful pending status, not a fixture score.
