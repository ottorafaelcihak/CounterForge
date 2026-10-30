# API reference (operational summary)

Base: `/api/v1`. The machine-readable contract is the committed
OpenAPI document at
[packages/contracts/schemas/openapi.json](../packages/contracts/schemas/openapi.json)
(title `CounterForge API`, version `1.0.0`, generated from the
FastAPI app in [services/api/app/main.py](../services/api/app/main.py)).
Regenerate/check with `make generate-contracts` / `make check-contracts`.
Interactive docs are served locally at `http://127.0.0.1:8000/docs`
when the API is running. This page summarizes the real routes; the
OpenAPI document wins on any discrepancy.

## Authentication and boundary

- Every route on the owner router requires
  `Authorization: Bearer <OWNER_AUTH_TOKEN>`
  (`require_owner` dependency). The browser never receives this token
  or any model key: it uses an opaque 8-hour HttpOnly/SameSite=Strict
  session through the allowlisted same-origin BFF proxy
  (`apps/web/app/api/backend/...`, `apps/web/app/api/session/...`).
- Anonymous (public) routes are exactly:
  - `GET /api/v1/health/live`
  - `GET /api/v1/public/demo`
  - `GET /api/v1/public/demo/assets/{public_asset_id}/content`
- `GET /api/v1/assets/{asset_id}/content` looks public in routing but
  calls `require_owner` in-handler; private asset bytes are owner-only
  and no legacy whitelist row grants anonymous access.
- Public demo reads serve only a published, sanitized snapshot with
  fresh public asset IDs; they expose no spendable or owner operation.
  See [public demo deployment](deployment/runbook.md).

## Route groups (owner-authenticated unless noted)

| Group | Routes |
| --- | --- |
| Health/setup | `GET health/ready` (owner; 200/503), `GET setup`, `GET fixtures/development-seed` (fixture mode only) |
| Projects/seeds | `POST/GET projects`, `GET projects/{id}`, `POST/GET projects/{id}/seeds`, `GET seeds/{id}` |
| Masks/review | `POST seeds/{id}/segment`, `POST/GET seeds/{id}/masks...`, approve/retire/revisions, `GET seeds/{id}/mask-review`, `POST seeds/{id}/masks/acknowledgements` |
| Contracts/targets | `POST seeds/{id}/contracts`, `GET seeds/{id}/contracts`, `GET contracts/{id}`, `POST contracts/validate`, `POST contracts/{id}/backgrounds`, `POST projects/{id}/targets`, `GET projects/{id}/targets`, `POST targets/{id}/check` |
| Experiments | `POST experiments`, `GET experiments/{id}`, pause/resume/cancel, `GET experiments/{id}/metrics`, `GET projects/{id}/experiments` |
| Mutations | `POST experiments/{id}/mutations`, `GET experiments/{id}/mutations`, `GET mutations/{id}`, history, `POST mutations/{id}/review`, `POST mutations/{id}/segment` |
| Findings/exports | `GET experiments/{id}/findings`, `GET findings/{id}`, `GET findings/{id}/evidence`, confirm, demo-approve, minimize/minimizations, `POST findings/{id}/exports`, `GET exports/{id}`, `GET exports/{id}/download`, `GET regressions` |
| Retests/jobs | `POST regressions/{export_id}/retest`, `GET regressions/{export_id}/retests`, `GET jobs/{id}`, `POST jobs/{id}/resume`, `POST jobs/{id}/cancel` |
| Demo snapshots (owner) | `POST experiments/{id}/demo-snapshot`, `GET demo-snapshots/{id}`, publish, revoke |
| Events | `GET experiments/{id}/events` (SSE), `GET experiments/{id}/event-history` |

List endpoints use signed scoped cursors (`limit` 1–100, default 20).
Mutating job/experiment/export calls require an `Idempotency-Key`
header (1–128 chars) where the OpenAPI operation declares it; a
repeated key reuses the original work.

## Errors

All declared error statuses (401, 403, 404, 409, 422, 429, 500, 501,
502, 503) return the same envelope:

```json
{"error": {"code": "<ErrorCode>", "message": "<sanitized text>", "correlation_id": "<id>", "retryable": false, "details": {}}}
```

Required `ErrorBody` fields are `code`, `message`, and
`correlation_id`; `details` is an optional structured object.

The exact `ErrorBody` fields are defined by `ErrorEnvelope` /
`ErrorBody` in the OpenAPI components. Messages are sanitized; no
credential, filesystem path, or provider payload is returned. The
readiness endpoint is the one exception in shape: it returns the
`Readiness` body (booleans only) with 200 or 503.

## Server-sent events

`GET /api/v1/experiments/{id}/events` is owner-authenticated (no
credentials in the URL), `text/event-stream`, with
`Cache-Control: no-cache` and `X-Accel-Buffering: no`. Resume with
the `Last-Event-ID` header; on reconnect the client refreshes the
authoritative experiment snapshot and deduplicates event IDs.
Terminal runs close the stream; `GET .../event-history?after=&limit=`
returns stored `StoredExperimentEvent` rows for replay. Known limit:
saved payloads over the 1 MiB client limit still need an
event-history fallback (see [limitations](limitations.md)).

## What is not exposed

No anonymous route creates projects, uploads seeds, starts
experiments, dispatches providers, downloads regression ZIPs, or
publishes/revokes a demo snapshot. Provider dispatch additionally
requires server-side live-mode gates; the API alone cannot spend.
