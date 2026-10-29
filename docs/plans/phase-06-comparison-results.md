# Phase 6 initial strategy comparison

Provenance: **REAL_PROVIDER**.

| Arm | State | Generated | Valid / invalid / review | Target calls / cap | Baseline / screen / confirm | Suspected / stable | API requests | Actual USD |
| --- | --- | ---: | --- | --- | --- | --- | ---: | ---: |
| ADAPTIVE | PAUSED | 0 | 0 / 0 / 0 | 3 / 7 | 3 / 0 / 0 | 0 / 0 | 5 | 0.006098 |
| RANDOM_BASELINE | COMPLETED | 1 | 1 / 0 / 0 | 4 / 7 | 3 / 1 / 0 | 0 / 0 | 5 | 0.005754 |

Descriptive tiny-sample comparison only; zero discoveries and unfinished arms are retained. No superiority claim.

Complete category costs, estimates, unresolved exposure, PRNG seeds, stop reasons and outcome denominators are retained in the companion JSON and declaration.

- One approved development seed per arm; no statistical superiority claim.
- Same available families and [0,1] strength range; Spark chooses strengths, random samples uniformly.
- Both arms retain the same independent validity and three-fresh confirmation gates.
- All planning, generation, SAM, baseline, screening and confirmation expenses are included.

The Spark arm is **unfinished/censored** after an ambiguous image-generation timeout:
3 correct baseline observations, one real Spark plan, no generated candidate or
screening observation. Its IMAGE charge remains unknown, conservatively bounded
at USD 0.012; confirmed arm cost is USD 0.006098. No automatic retry was made.

The random arm used predeclared seed `20261030` (`random-initial-v2`) and selected
`brightness_shift` at strength `0.2198`. Three fresh seed counts were correct;
SAM detected zero usable instances. Otto independently approved the exact retained
candidate (SHA-256 `859299b0ba43fa6d56a24af638a982df51e9fe2451a08eb78105205f3d226672`),
count 5, semantics intact and visibility clear. Its screening count was correctly
5, so no confirmation was required and the bounded arm completed. Confirmed arm
cost is USD 0.005754. Human resolution is not evidence of successful SAM masks.

Combined Phase 6 usage: **10 calls / USD 0.011852 confirmed**, plus **up to USD
0.012 unresolved**, within the approved additional USD 15 / 30-call envelope.
Neither arm found a suspected or stable failure. The genuine stable-failure and
live regression-export goals remain unmet; fixture exports demonstrate the
pipeline only. An incomplete arm cannot establish adaptive lift or parity.

The declaration was written before any provider reservation. Original source,
exact approved masks, 480×240 resolution, target/options, permitted families and
strength range, verifier and 3-fresh rule are shared. Each arm had ceilings of one
candidate, 7 target calls, USD 7.50 and 7,200 seconds. The sample is one development
seed per arm; this is not the full held-out benchmark planned for Phase 8.
