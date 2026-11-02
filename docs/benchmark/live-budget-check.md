# Live budget worksheet — no spending approval

Checked November 2, 2026 against the official
[Meta Model API pricing page](https://dev.meta.ai/products/meta-model-api).
For standard `muse-spark-1.3`, it lists USD 1.25 input, 0.15 cached input and
4.25 output per million tokens; Muse Image is USD 0.01/image and SAM is
USD 2.50/1000 images. These match the base rates in the current `pricing.toml`.
No model entitlement, free-credit balance or actual account billing was checked.
The contributor variant is a different choice; do not silently substitute it.

The existing `PriceBook.upper` deliberately reserves the full configured
1,048,576-token Spark context plus the allowed completion, rather than predicting
the eventual bill from a small image. Offline calculations from that exact code:

| Operation | Conservative request reservation, USD |
| --- | ---: |
| Spark, 64 output tokens (current UI target default) | 1.310992 |
| Spark, 4096 output tokens | 1.328128 |
| Muse Image, including configured local margin | 0.012 |
| SAM, including configured local margin | 0.0030 |

For the draft global 600-call cap, multiplying the largest request bound gives a
very loose USD 796.8768 aggregate ceiling. This is **not** an expected bill, an
approved budget, or an executable protocol: the protocol model accepts at most
USD 150 and the proposal remains USD 5. Do not raise either limit automatically.
Normal reconciliation replaces reservations with actual usage when known;
unknown/missing usage stays conservatively exposed. A small global allowance can
stop the campaign, especially while reserving three fresh confirmations, even
when earlier actual calls were inexpensive.

Before requesting authorization, bind the actual selected target/output limit,
dataset/protocol hashes, candidate and target-call caps, current price revision,
finite operator allowance, runtime and expiry. Explain whether the user chooses
a tightly bounded potentially censored pilot or a separately sized campaign.
The primary arm caps must remain matched. Censored comparisons must be withheld
where required by the existing report policy; no complete-run cost is promised.
