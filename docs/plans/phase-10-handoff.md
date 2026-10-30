# Phase 10 — Release preparation handoff

Prepared on 2026-10-30. **Release preparation is implemented; publication and
submission are pending.** The owner chose MIT for original code. The frozen
product runtime is `8029ff28421a0bc463af18934de153cd16302600`; Phase 10 changes
documentation, release tooling, CI and tests, with no application/schema change.
The Git commit containing this handoff binds the Phase 10 delta. The
[source manifest](../releases/source-package-manifest.json) records candidate
source hashes; generated release metadata is excluded to avoid self-reference.

## Delivered

- Fresh setup and model-role documentation, MIT license, attribution, API guide,
  locked dependency inventory and preserved third-party notices.
- A [32-row acceptance matrix](../submission.md), machine-readable
  [release index](../releases/evidence-index.json), and offline validator that
  rejects inconsistent readiness claims without making external calls.
- Deterministic offline video capture and synchronized captions. The private
  draft is exactly 180 seconds, 180 decoded frames, 1280×720 at 1 fps, silent
  with burned captions. All seven encoded scene midpoints were inspected.
  [Video verification](../releases/demo-verification.json) binds its SHA-256,
  scene hashes and source provenance. No media is published by this commit.
- An allowlisted read-only TLS proxy example. Nginx syntax and 45 local
  method/route/header assertions passed with disposable dummy upstreams.
  [Receipt](../releases/proxy-rehearsal-results.json) records its source hash
  and image digest. This is policy verification, not a deployed-product receipt.
- An active-call Pause regression: the in-flight confirmation drains, evidence
  persists, and no next dispatch starts. Capture tests retain actual fixture
  rejection UI and a portable bundle for the demo.

## Verification and scope

Clean Git archive of the frozen runtime, SHA-256
`91a40698abce0fcfe9453259a70c42bf1065af96088b196deddf4391a9c9845a`,
was installed using locked dependencies without the development database,
environment file or attachments. Results:

| Check | Measured result |
| --- | --- |
| Frozen lint/types/contracts generation | PASS; Ruff 220 files, mypy 115 sources, web lint/types |
| Frozen unit / contract / Node tests | 245 / 173 / 13 passed |
| Frozen fresh PostgreSQL suite | 280 passed, 2 expected Docker rehearsal skips |
| Frozen browser suite / operational rehearsal | 15 / 2 passed |
| Frozen production build / portable export verification | PASS |
| Phase 10 current workspace lint/types/unit/release structure | PASS; 266 unit tests; 0 structural errors |
| New active-call Pause / capture browser checks | 1 passed (2.35s) / 2 passed (24.51s) |
| Actual fixture export outside checkout | Healthy exit 0; broken and replay exit 1 with expected assertion failure |
| Local fixture p95 latency, 20 samples | Live 0.60ms; ready 9.14ms; projects 1.57ms; public latency unmeasured |
| Locked advisory audits | Python 62 and npm 467 dependencies; 0 known vulnerabilities at check time |
| License inventory | 62 Python, 467 npm, 442 notice files; scope recorded, no compatibility certification |
| Gitleaks 8.30.1 | History/source/final source + ZIP/media: one explicit dummy rehearsal token, triaged; 0 untriaged/real credentials |

The generated Next.js server/cache build contains 13 private framework keys.
These are excluded from source/media archives and public static routes;
browser `.next/static` scanning found zero matches. They are not classified as
false positives. Scanner summaries omit secret values. See
[security summary](../releases/security-summary.json) and
[dependency audit](../releases/dependency-audit-summary.json).

The actual fixture bundle SHA-256 is
`34435504971aa19f9b8c16068e03667922e41d445f9a25f8743ada26c9e2c08c`.
It expects five objects; the deliberately broken fixture returns four.
This demonstrates the runner, not a real model weakness. Rejection UI uses a
simulated additional-object review; the still itself is not claimed to contain
a sixth object. Historical real-provider results are attributed to their own
records, not to a new run at the frozen runtime revision.

Full DB/browser suite results above apply only to frozen8029. The Phase10
test-only delta was checked with the targeted Pause and capture browser cases,
not a full current-workspace DB/E2E rerun; no app/schema code changed.

## Remaining release gates

Real stable findings remain **zero**, SAM produced **zero usable instances**
on the development illustration, and the Phase 08 measured campaign has not
run. No live regression export or comparative lift is claimed. These are
visible unmet goals, not reasons to fabricate evidence or start paid calls.
Phase 10 used **0 new Meta calls, $0 actual, $0 unknown**.

The existing repository was already public; an unauthenticated request for the
frozen README returned HTTP 200 and matched its Git blob. The new Phase 10
commit is not covered by that access receipt until pushed. Product/video URLs,
public access receipts and submission confirmation remain absent. The private
snapshot remains DRAFT with zero findings. Deployment needs a real destination,
hostname/TLS and an explicitly published, rights-reviewed snapshot; media
publication needs its destination and approval. No public paid controls are
prepared.

Authoritative public event pages confirmed November 4, 2026, 21:00 ET
(November 5, 03:00 Europe/Madrid) on the check date. The participant guide did
not load; final rules/form fields, entrant identity, track and eligibility
remain unverified. See [organizer check](../releases/organizer-guidance.json).
The submission document is a draft and is not a receipt. No Phase 11 work
starts before the release decision.

## Team review

Codex (`gpt-6.1-sol`) mapped acceptance and verification gaps. Muse
(`muse-wonder-1.0`) implemented the bounded documentation/tooling/test delta.
The coordinator integrated measured evidence, captured/verified the video and
ran final checks. Claude Code (`claude-opus-5`) independently reviewed the completed delta.
Readiness parsing, per-row revision attribution, scene hash checks and early
encoder-exit handling were corrected with regression coverage. Documentation
now states the full-suite scope explicitly. The focused re-review outcome is
recorded in [review summary](../releases/review-summary.json).
