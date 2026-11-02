# Final recording plan — waiting for real evidence

This is a 180-second working plan, not a claim that the participant portal permits
exactly this length. Confirm its actual constraints before final rendering.
No current final video exists. The historical draft receipt is not its substitute.

| Time | Capture | Required evidence and narration |
| --- | --- | --- |
| 0:00–0:20 | One rights-cleared reviewed source image | Explain the counting question, real human label and why an unchanged answer is expected. Show actual approval, not a simulated review. |
| 0:20–0:45 | Its locked contract and approved masks | Show protected regions and the three-fresh-confirmation rule. Explain human validity review. |
| 0:45–1:20 | Actual retained planner/generator/perception records | Name observed models and limitations. Distinguish fresh live capture from replay. Never imply an unrun model step succeeded. |
| 1:20–1:45 | One actual rejected or ambiguous mutation | Explain the genuine validity reason and preserved review history. If none exists, show the actual review workflow and say so. |
| 1:45–2:10 | Audited outcome | Show a genuine audited finding if one exists. Otherwise say zero confirmed failures and show the real outcome; do not substitute an unlabeled fixture. |
| 2:10–2:40 | Verified regression or methodology runner | Use a genuine eligible export if available. Otherwise label the fixture segment LOCAL_FIXTURE / PIPELINE DEMONSTRATION throughout, including its deliberately failing assertion. |
| 2:40–3:00 | Source-reconciled benchmark summary | Raw counts/denominators, matched caps, actual/estimated/unknown spend, exclusions and uncertainty. Withhold lift if unsupported; full_system stays a separate ablation. |

Prepare source stills, narration and captions only after evidence exists. Every
frame needs provenance (real retained run, recorded replay, or synthetic fixture).
The existing capture script burns a RECORDED STILLS, NOT REALTIME banner and
produces silent WebM at one frame per second. It does not record narration.
If the portal requires a different codec/container or audio, prepare a compatible
export only after confirming those requirements; the current bundled FFmpeg is
a limited build, not a general audio/editing suite.

For each scene retain: source run/experiment/candidate IDs, exact source-image
SHA-256, start/duration, caption/provenance, source bundle hash and reviewer/media
permission record. Fill a private copy of `docs/demo/scenes.example.json`, replacing
its historical labels/counts with the actual final evidence. Do not reuse its
specific five-object captions for another image.

Use the existing command shape (verified through `--help`):

```bash
python -m scripts.capture_release_demo --root <REVIEWED_SOURCE_ROOT> \
  --scenes <FILLED_SCENES.json> --output-dir <NEW_PRIVATE_VIDEO_DIR> \
  --ffmpeg <ABSOLUTE_FFMPEG_PATH> --expected-duration 180
```

Encoding success alone is insufficient. Decode the full video; check the actual
frame count, duration, 1280×720 dimensions and audio status. With the prepared
Playwright FFmpeg, full VP9 decode/re-encode to a new private WebM works; its build
does not support the null muxer or MJPEG encoder. Inspect a decoded midpoint for
every scene and the rendered proof stills, including text legibility and redaction.

Save a new verification receipt binding output hash, source hashes, code revision,
captions, stream checks and inspection. Keep the seven-second tool-smoke receipt
separate from final media. Request approval for that exact final file before upload.
