# Live testing, final recording and submission handoff

Prepared November 2, 2026. **Local preparation is not live-run or release approval.**
Start with the [current verification receipt](../releases/local-final-readiness.json).
The app is at `http://localhost:3000`; sign in with the owner token in local `.env`.
Never show that token or a model key in a recording.

An empty **Phase 08 final live benchmark** project is prepared at
`/projects/5b5f65fc-64b4-474e-bdbb-6ca784301dc8`. No seeds, masks, targets or spending
approvals were invented for it.

## 1. Finish the human dataset gate

Open `.cache/phase08/private/review/index.html` locally and complete
[the twelve-image worksheet](../benchmark/drafts/image-review.md).
The original images are in `docs/images`; normalized private copies and verified
hashes are prepared. Confirm independent counts, ambiguity, redistribution rights,
reviewer identities/times and the proposed eight development/four held-out split.
The packet does not save form entries; use the worksheet or print it.
No count, right or mask may be approved automatically.

## 2. Prepare the exact supervised run

Follow [the detailed Phase 08 guide](phase08-human-action-guide.md) for actual
upload, mask-review, target, freeze, secure credential and audit commands.
Codex can import completed reviews, verify normalization and hashes, prepare the
two ordered contracts per case, and bind actual target/project IDs only after
that evidence exists. The draft USD 5 / 600 global calls / 3600 seconds is a
proposal, not spending authority or a completion-cost quote.

The [current pricing/reservation worksheet](../benchmark/live-budget-check.md)
shows why this small proposal may stop early; no cap was increased.

Before requesting paid approval, show the exact dataset/protocol hashes, target
settings, finite call/dollar/runtime limits, expiry and a checked operator allowance.
The user then separately authorizes that concrete run and enters the key locally.
Do not reuse a Phase 07 allowance or turn on LIVE across the ordinary stack.

The local serial-review override has been prepared, but is **not activated**:
`.cache/phase08/compose.review.yaml` sets review API `ENQUEUE_JOBS=0`.
At the authorized preflight, stop ordinary workers for the benchmark database,
apply this override, confirm shared database/owner/artifact storage, and let only
the evaluation CLI dispatch. Keep the ordinary providers disabled.

```powershell
# Current normal local stack (no paid calls):
docker compose -f compose.yaml -f .cache/compose.setup.yaml up -d --no-build --wait

# ONLY at the separately authorized serial-run preflight:
docker compose -f compose.yaml -f .cache/compose.setup.yaml stop worker
docker compose -f compose.yaml -f .cache/compose.setup.yaml -f .cache/phase08/compose.review.yaml up -d --no-build api
```

The CLI must mount the existing `counterforge_artifacts` volume at its configured
artifact root and use the same database/project as the review app. Preserve the
same durable run directory and immutable protocol when resuming. Review waits
consume the global runtime/expiry. Unknown charges require reconciliation before
retries. Once all work is terminal and reconciled, restore the normal stack with
the first command, omitting the review override.

## 3. Capture and audit real evidence

Supervise semantic/visibility pauses through the existing mutation review UI.
After the run, inspect all arm states and source receipts, then prepare the blinded
packet. An independent human audits every headline finding and the random
accepted/rejected sample; Codex imports only completed decisions. Verify the
source/report bundle before writing performance claims. A zero-finding result is
valid; fixture success does not establish a real failure or adaptive superiority.

## 4. Record the final video

Use [the final recording plan](../demo/final-recording-plan.md). A new local
seven-second recording-tool rehearsal is available under
`.cache/final-qa/video-smoke/render/demo.webm`. It is labeled PRIVATE TOOL REHEARSAL,
shows only the current sign-in screen, and is **not the final submission video**.
The older 180-second video's verification record is historical; its media files
were not found on this host and must not be presented as currently available.

After actual source capture, use the existing `scripts.capture_release_demo`
workflow with explicit source-image hashes and an explicit FFmpeg path. The
prepared Linux image `counterforge-final-qa` includes Chromium and bundled FFmpeg.
The capture tool makes a recorded-stills video, not realtime screen capture or
narration; label that continuously. Record realtime material separately if desired.
Verify the whole encoded stream, duration, dimensions, audio status and every
scene midpoint. Check for secrets/private media before requesting approval.

## 5. Review submission materials, then authorize publication

[The submission content draft](../demo/submission-content-draft.md) is ready for
actual results and links. Product/video public URLs and submission receipt are
still absent. Publishing source commits containing private inputs, publishing
media, deploying a public product and submitting to organizers need their
separate explicit approvals. Only reviewed, rights-cleared material is eligible.

The official event page was checked November 2 and still lists November 4,
21:00 ET (November 5, 03:00 Europe/Madrid) as the deadline. The participant details
page returned only a loading shell; final form requirements, team eligibility and
accepted video format/duration remain unverified. Open the actual participant
portal and inspect those requirements before final encoding/submission.
Sources: [Meta event](https://dev.meta.ai/events/global-hackathon),
[participant details](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details).

After authorized publication, Codex checks the actual repository/product/video
links in an unauthenticated session and confirms public paid controls are blocked.
After authorized submission, retain the actual confirmation and timestamp.
Until then, `release_ready` remains false.
