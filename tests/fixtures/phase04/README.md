These are synthetic five-object raster specimens, with retained source, masks,
backgrounds, union, composite, absolute RGB diff, recipes, measured metrics and
hashed validation reports. `manifest.json` lists exact file hashes and labels.
No live provider was called. No real human reviewed or approved these images.
The contract label, mask approvals and `SYNTHETIC_TEST_REVIEWER` decisions are
explicit test doubles; the manifest's actual human review remains PENDING.

- `accepted-after-simulated-review`: source pixels preserved on a different
  neutral background. Automatic REVIEW_REQUIRED; a test-only human decision
  exercises VALID. Actual acceptance requires an authenticated human review.
- `object-removal`: one of five rectangles removed; structural INVALID despite
  truthful updated output/diff hashes and metrics. Human review cannot erase it.
- `ambiguous-visibility`: foreground preserved exactly, but the entire background
  matches its color. REVIEW_REQUIRED even after simulated ambiguous review.
- `seam-only-control`: background differs only in the measured one-pixel exterior
  contour ring. Foreground/interior remain exact; evaluate seam effects in Phase 08.

`generate.py` rebuilds fixtures offline using the project Python environment.
Application viewing/export must use retained bytes, never regenerate images.
These development specimens do not calibrate thresholds or replace held-out data.

Compositing uses strict binary original pixels, with no feathering. Any feather
could repaint thin boundaries and lacks calibrated safe margins. The one-pixel
Chebyshev ring is a measurement region, not permission to alter protected pixels.
Pillow exact LUTs, pixel compositing and min/max raster filters cover the required
operations, so NumPy and OpenCV would add dependencies without a needed operation.
