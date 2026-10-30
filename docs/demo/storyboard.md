# Demo storyboard — exactly 180 seconds (private review draft)

A private **180-second silent captioned draft** has been rendered and verified:
180 decoded frames, 1280×720, 1 fps, VP9/WebM, with all seven midpoint
frames inspected. It uses recorded stills, not realtime playback.
The [verification record](../releases/demo-verification.json) binds its hash,
source scenes and fixture bundle. Media publication remains pending.
Captions: [captions.vtt](captions.vtt). Reusable template:
[scenes.example.json](scenes.example.json). Offline capture tool:
[capture_release_demo.py](../../scripts/capture_release_demo.py).

## Honesty rules (continuous, not a disclaimer at the end)

- A provenance banner is visible in **every** frame, identifying recorded source, real-provider records or `LOCAL_FIXTURE`,
  followed by `RECORDED STILLS, NOT REALTIME`.
- No fabricated real wrong answer, benchmark, or live call is shown.
  The real recorded search found **0 stable findings**; the fixture
  segment demonstrates the regression pipeline only.
- `3/3`, where mentioned, means three fresh trials under recorded
  settings, never universal reproducibility.
- No unapproved real-provider image is used. Only the CC0-recorded
  fixture source below and root-captured local UI/runner frames.

## Scenes (§21 timings)

| Time | Duration | Screen | Provenance label | Must be true |
| --- | ---: | --- | --- | --- |
| 0:00–0:20 | 20s | Seed picture; recorded correct count 5 | RECORDED SOURCE — HUMAN COUNT 5 | Human-verified count on the fixture source |
| 0:20–0:45 | 25s | Contract and protected masks | LOCAL_FIXTURE — SIMULATED REVIEW | Explicit invariants; human mask approval shown |
| 0:45–1:20 | 35s | Spark plan, Muse Image/SAM process (recorded) | REAL PROVIDER RECORDS — NO NEW CALLS | Recorded provider outputs; SAM 0 usable instances disclosed |
| 1:20–1:45 | 25s | Candidate gallery, one rejection | LOCAL_FIXTURE — ACTUAL RECORDED UI | Actual INVALID UI after simulated additional-object review; no sixth object is claimed in the image |
| 1:45–2:10 | 25s | Search outcome | REAL PROVIDER RECORDS — ZERO STABLE FINDINGS | Screenings were CORRECT; no real wrong answer is invented |
| 2:10–2:40 | 30s | Regression artifact + terminal pytest | LOCAL_FIXTURE — ACTUAL OFFLINE PYTEST OUTPUT | Healthy runner passes; broken/replay assertion fails as designed |
| 2:40–3:00 | 20s | Metrics and limits | REAL PROVIDER RECORDS — NOT A BENCHMARK | Phase 05–07 recorded counts/costs only; Phase 08 campaign not run |

The 1:45–2:10 slot replaces §21's "confirmed target failure" with
the truthful recorded outcome, because no such failure exists.
Do not re-label the fixture runner failure at 2:10–2:40 as that
missing real discovery.

## Capture inputs root must supply

| Input | Value / location |
| --- | --- |
| Fixture source | `fixtures/phase03/five-blue-containers.png`, SHA-256 `fffd1c6bb468fd41623a5936a60b64daf438e0f438c8d049e883f65c7bdb7a5a` |
| Phase 05 experiments | `7ec052b1-6db5-4db1-8ce1-e37bbb806ba2`, `9bff00b1-81a8-43bf-82cc-42349cc6d791` (recorded in [Phase 05 handoff](../plans/phase-05-handoff.md)) |
| Phase 05 candidate | `dd1c33f4-228c-4391-9ad0-33925dc267cf`, image SHA-256 `992ef9aedd6b7a86bc7731975de55de105bb26708d7d265067ffac2e84d436cc` |
| Phase 07 candidate | Approved SHA-256 `859299b0ba43fa6d56a24af638a982df51e9fe2451a08eb78105205f3d226672`; record: [phase-07-live-results.json](../plans/phase-07-live-results.json) |
| Runner evidence | Root-captured actual `pytest` healthy (exit 0) and broken/replay (exit 1) runs via `make verify-export` semantics |
| Screenshots | Root-captured from the local product journey and a rejection view; each file's SHA-256 filled into the scene manifest before recording |

Root fills `image` and `sha256` per scene in a copy of the scene
template (integer durations, contiguous `start_seconds`), then runs:
`python -m scripts.capture_release_demo --scenes <filled>.json --output-dir <out> --ffmpeg /absolute/path/to/ffmpeg` (add `--expected-duration 7` for a 7-frame smoke list).
Inspect frames for keys, private images, browser URLs, and
secret-bearing terminal output before any publication decision.

FFmpeg is an external prerequisite: supply a binary with MJPEG input, VP9
encoding and WebM output. The retained draft used Playwright’s bundled FFmpeg;
the capture script does not download or install it. Template empty image/hash
fields are preparation placeholders; populated scenes require matching hashes.
