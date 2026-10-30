# Attribution

This file inventories provenance. It does not assert a legal name,
copyright ownership, or a project license that the owner has not chosen.

## Project license: MIT (owner-confirmed)

The original CounterForge code in this repository is licensed under
the **MIT License**; see [LICENSE](LICENSE)
(`Copyright (c) 2026 CounterForge contributors`). The owner confirmed
this choice on 2026-10-30 (canonical template:
<https://opensource.org/license/mit>). No personal legal name is
asserted; the Git credit below remains metadata only.

This MIT grant covers original project code only. It does **not**
relicense third-party work: dependencies keep their own licenses
(including the custom SAM License and LGPL/MPL/CC-BY terms noted below),
and no blanket legal-compatibility or asset-rights approval is claimed.

## Original work

CounterForge's application code, contracts, evaluation harness, docs,
and demo materials in this repository were created for this project
during the hackathon period recorded in Git history (first commit
2026-10-27). This includes the planner/orchestrator, Visual Test
Contract, validity verifier, regression exporter, web UI, and the
procedural development fixture below.

Git metadata credit (metadata only, not a legal-name assertion):
all 10 commits at the frozen revision are authored as
`otto <ottocihak@hotmail.com>` (`git log` / `git shortlog`).
No other Git author is recorded. This does not invent a legal name,
team, employer, or ownership claim for that author.

## Sample fixture (1 source)

| Asset | Provenance as recorded | Hash |
| --- | --- | --- |
| [five-blue-containers.png](fixtures/phase03/five-blue-containers.png) | Original procedural artwork created for CounterForge; manifest records `CC0-1.0` | `fffd1c6bb468fd41623a5936a60b64daf438e0f438c8d049e883f65c7bdb7a5a` |

The fixture manifest is [manifest.json](fixtures/phase03/manifest.json).
It records one source file only. It contains **no separate rights
notice file**, and its recorded CC0 statement is **pending owner
review** before any public redistribution relies on it. It is a
synthetic development fixture, not held-out evaluation data and not
evidence of a real model weakness.

No provider-generated image, private seed, or file from `data/*-live`
is included as a redistributable sample asset.

## Dependencies (not original work)

Runtime and development dependencies are third-party work under
their own licenses. The authoritative inventories are the lockfiles,
not this summary:

- Python: [uv.lock](uv.lock) and [pyproject.toml](pyproject.toml)
  (FastAPI, Uvicorn, SQLAlchemy, Alembic, psycopg, Pillow, Redis/RQ,
  Pydantic via workspace packages; dev: pytest, Ruff, mypy, Playwright).
- Web: [apps/web/package-lock.json](apps/web/package-lock.json) and
  [apps/web/package.json](apps/web/package.json)
  (Next.js, React, React DOM; dev: TypeScript, ESLint, Tailwind CSS).
- Workspace packages under `packages/` are project code, not
  third-party dependencies.

The locked-dependency [inventory](docs/releases/dependency-inventory.json),
[license and notice texts](docs/releases/THIRD_PARTY_NOTICES.txt), and
[audit summary](docs/releases/dependency-audit-summary.json) are retained.
The inventory covers installed Linux notices plus two hash-verified locked
Python wheels. It does not certify every optional platform or OCI layer.

Notices needing particular attention in that root inventory (from
installed metadata, not a compatibility claim):
`meta-sam-parser` under the SAM License (see the
[provider record](docs/plans/phase-03-provider-record.md));
`psycopg`/`psycopg-binary` (LGPL-3.0-only per installed metadata);
Sharp/libvips (LGPL variants) and Lightning CSS/`axe-core` (MPL)
in the web lockfile's distributed subset; `caniuse-lite` (CC-BY-4.0).
Root assesses the actually distributed subset and retains applicable
materials; container base images and system tools are also in scope.
Root's locked-dependency inventory summary is copied at
[docs/releases/dependency-inventory-summary.json](docs/releases/dependency-inventory-summary.json)
(python_locked 62, npm_locked 467, notice_files 442, from two
hash-verified locked wheels); the full inventory and notices file
are linked above. Scope is installed notices, not all-platform
OCI or model-rights certification. That inventory is a scope record,
**not** legal compatibility approval.

## Provider models (services, not redistributed assets)

Meta Model API models are remote services, not code or weights
included in this repository: Muse Spark 1.3 (planner and target),
Muse Image 1.0 (scene generation/editing), SAM 3.1 (segmentation).
See [the provider record](docs/plans/phase-03-provider-record.md).
No provider output is redistributed here without recorded approval.
