# Phase 08 benchmark preparation ledger

Date: November 2, 2026. Inspected baseline commit:
`8e14731ed4f752b610484fbfb07c7108a98d4204`, branch `master`, clean at start.

**Overall: WAITING_FOR_USER. The genuine benchmark is not complete.**
This preparation made zero paid provider calls and authorized USD 0. No genuine
benchmark measurement, approved dataset, independent audit or superiority claim
was produced. Historical development runs are not benchmark evidence.

## Acceptance criteria and ownership

Reuse the existing dataset/protocol/runner/audit/report machinery. Prepare a
beginner guide, twelve unapproved review slots, matched protocol/binding drafts,
and tested no-money commands. Preserve exactly 8 development / 4 held-out cases,
independent human rights/label/audit gates, same primary target-call caps and
three fresh confirmations. Report the generator arm separately. No provider,
rights, billing, publication or submission action is authorized.

Coordinator owns only this documentation and draft JSON. Evaluation runtime and
product source are unchanged. A native Codex agent independently inspected code
and reviews the completed documents read-only; it is not an agent-team bridge
worker. Worker ID: `/root/benchmark_review`, provider Codex, inherited model
default, workspace `C:\Users\ottoc\Projects\CounterForge`.
No agent-team tools were exposed, `Get-Command agent-team,muse` found neither CLI,
and searches of installed skills and local agent repositories found no
`develop-with-agent-team` skill. Claude CLI installation was found, but no bridge
provider run/authentication was established. Claude and Muse participation is
**not claimed**; three-provider review remains unavailable on this host.

## Gap analysis against the requested benchmark

| Gate | Actual evidence | Status |
| --- | --- | --- |
| Existing machinery | `evaluations/{dataset,protocol,runner,adapter,audit,packet,report,cli}.py`; Phase 08 handoff | COMPLETE: implemented methodology |
| Exactly 12 reviewed cases | Twelve user-supplied PNGs exist in `docs/images`; none has completed label/rights review | WAITING_FOR_USER |
| Historical external proposals | Windows `/tmp` check false; fresh Linux test-container `/tmp/counterforge-phase08-dataset` check false | BLOCKED: old path cannot be reused here |
| Current source inventory | `fixtures/phase03` contains synthetic software fixtures; phase04 contains retained mutation test specimens, not a new 12-case independent review | No automatic admission |
| Labels/rights/split review | `docs/benchmark/drafts/image-review.md`; proposed 8/4 slots, every human field blank | WAITING_FOR_USER |
| Dataset freeze | `drafts/dataset.json` has computed hashes/dimensions; counts/rights remain null | BLOCKED |
| Protocol design | `random_det` vs `spark_det`, brightness only; `full_system` separate ablation; FEEDBACK_V1/RANDOM_V1 retained | Draft prepared, not frozen |
| Target/bindings | No selected real target or 12 imported reviewed seed bindings | WAITING_FOR_USER |
| Spend/expiry/key | No Phase 08 authorization; proposal caps grant no authority | WAITING_FOR_USER |
| Independent blinded audit | Existing packet/audit implementation tested; no real candidate population or human decisions yet | BLOCKED |
| Genuine source/report bundle | Only labeled methodology fixture rehearsed | BLOCKED |
| Publication/submission | No action taken or authorization inferred | WAITING_FOR_USER |

The repository's existing release gate `phase08-measured-evaluation` stays pending.
See [human action guide](../guides/phase08-human-action-guide.md) for a consolidated
checkpoint. No frozen protocol/dataset or real authorization was created.

## Execution record

1. Read `AGENTS.md`, `docs/plans/plan-08.md`, its referenced PRD/evaluation
   specification sections, Phase 08 handoff, `docs/evaluation.md`, CLI,
   templates and the associated tests. Confirmed implementation exceeds the
   historical unchecked plan boxes; those boxes are not fresh evidence.
2. Created the human-action guide as the first documentation deliverable.
   Prepared `drafts/{dataset,protocol,bindings}.json` and twelve-row review form.
   Initially created an empty private assets destination at
   `C:\Users\ottoc\Projects\CounterForge\.cache\phase08\private\assets`.
   No labels, licenses, reviewer identities or timestamps invented.
3. Reused prepared Linux image `counterforge-dev-checks`, Python 3.12.16,
   `/opt/counterforge-venv` (named Docker volume). Source was mounted read-only;
   output was mounted at `/evidence` from host `.cache/phase08`.
4. Created separate disposable database `counterforge_phase08_test_20261102`
   with `docker compose exec -T postgres createdb -U counterforge
   counterforge_phase08_test_20261102` (exit 0). Integration fixtures create/drop
   their own random schemas there; the application database was not used.
5. Ran every documented evaluation subcommand's `--help` (exit 0), then:

   ```text
   python -m evaluations.cli fixture --output /evidence/rehearsal-v1
   NO MONEY / METHODOLOGY_FIXTURE — no approved dataset, provider calls or model findings.
   python -m evaluations.cli verify-bundle /evidence/rehearsal-v1
   Bundle file hashes and report-to-source reconciliation verified.
   ```

6. Tested original incomplete templates with `validate-dataset`, `freeze-dataset`
   and `freeze-protocol`: each exited 2; no frozen output created. These checks
   intentionally fail. Missing rights/labels, duplicate/split hashes, incomplete
   arms, zero denominators, report tampering, audit binding, global guards and
   durable review/resume are additionally exercised by the test suite below.
7. Ran the prepared script `.cache/phase08/run_checks.py` through Docker with
   `MODEL_API_KEY=` empty, `PROVIDER_MODE=DISABLED`, `RUN_META_LIVE_TESTS=0`, and
   PostgreSQL's container network namespace (explicit loopback disposable test DB).
   The script injects only the local test database password in memory and prints
   no credentials. Exact focused test command:

   ```text
   python -m pytest tests/unit/test_dataset_manifest.py tests/unit/test_audit_sampling.py tests/unit/test_evaluation_metrics.py tests/unit/test_evaluation_completion.py tests/unit/test_evaluation_global_guards.py tests/integrations/test_evaluation_protocol.py tests/contracts --allow-hosts=127.0.0.1 -q --tb=short -p no:cacheprovider
   231 passed in 38.78s
   ```

Evidence retained privately in `.cache/phase08/{cli-help.txt,rehearsal.log,tests.log}`
and `.cache/phase08/rehearsal-v1/`. These local paths exist; fixture contents are
invented methodology only. No genuine results are inferred from their numbers.
Browser E2E was not rerun for this documentation-only change; the existing
database review/pause/resume integration scenarios passed. No application bug
requiring a runtime change was established.

## Proposed envelope, not spending approval

The unapproved draft proposes 10 total target calls per arm/case, 3 candidate
attempts, global 600 provider calls, USD 5 exposure, 3600 seconds, PRNG seed 17.
Arithmetic ceilings: 360 total target calls; 240 primary + 120 ablation;
108 baseline calls included. Global calls include planner/Image/SAM overhead.
No completion-cost estimate is asserted without current verified pricing and
worst-case request sizes. USD 5 is a proposed hard allowance, not a claim the
campaign will finish. Target fingerprint, dataset hash and expiry remain unset.

## Next checkpoint

User: complete independent label/rights review of the twelve supplied files and confirm
split, name a target and reviewer availability. Then Codex can normalize and
validate real evidence, prepare actual bindings and estimate the paid envelope.
Only after review comes separate Phase 08 paid authorization and secure local
key entry. Candidate reviews, independent audit and publication stay separate.
No benchmark performance figures or judge-facing positive result is available.

## User-supplied asset preparation (subsequent step)

The user supplied `C:\Users\ottoc\Projects\CounterForge\docs\images`.
Inspected all twelve PNG filenames/sizes; Linux `normalize_image` decoded all
twelve and produced private canonical copies. Result: 12 source hashes distinct,
12 normalized hashes distinct. No duplicate bytes were found; a human must still
check near duplicates, scene overlap and split membership. Files were sorted by
original filename for proposed case IDs 01–12, first eight development, last four
held-out; no target outcomes informed this proposed order.

Actual private paths: `.cache/phase08/private/assets/{source,normalized}/`,
`.cache/phase08/private/asset-inventory.json`, and printable
`.cache/phase08/private/review/index.html`. Each normalized file maps to its
original filename in the inventory and tracked review worksheet. The normalized
images and originals were not staged or redistributed. Source filenames suggest
generated assets; that does not establish their rights or verified labels.
The user supplied only a location, not counts, rights decisions, target choice
or spend authorization. Human attestations remain blank.

## Final independent review and checks

Native Codex review identified shared artifact-store requirements, interference
from ordinary queued workers, and abbreviated seed/mask instructions. All three
were resolved and the reviewer found no material remaining issues. No general
worker was switched to LIVE. This review does not establish three-provider participation.

Final packet QA verified every HTML image link and all 24 private image hashes,
and reconfirmed the twelve original input hashes. The manifest has proposed 8/4
slots and all count/rights/review-time approvals remain null. Draft JSON and the
release index parse; git diff --check passed. Linux scripts.release_checks exited
0: release_checks: 0 error(s); release_ready=False; structural validation only,
not release readiness; external links/scanners not verified.

Only documentation/drafts are committed; user-provided docs/images remains untracked.
