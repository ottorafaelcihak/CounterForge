# Public demo deployment runbook (PUBLIC ONLY, not deployed)

Nothing here is deployed, and no domain, TLS certificate, or
autodeploy is invented. Root owns config checking, publication
authorization, and any actual access test.

## Allowed public surface (allowlist, deny by default)

| Path | Upstream | Why |
| --- | --- | --- |
| `GET /demo` | web `:3000` | Sanitized read-only gallery page (SSR loads the API directly; no web BFF manifest route exists at `/api/public/demo`) |
| `GET /api/v1/public/demo` | API `:8000` | Public manifest served by the API; resolves its `content_urls` |
| `GET /api/v1/public/demo/assets/{uuid}/content` | API `:8000` | Snapshot images with fresh public IDs only (canonical UUID pattern) |
| `GET /api/public/demo/assets/{uuid}/content` | web `:3000` | SSR image URLs emitted by the web app |
| `GET /_next/static/...` | web `:3000` | Required static assets for `/demo` |

Only GET/HEAD are allowed publicly (server-level `405` for other
methods), and public upstreams receive no `Authorization`/`Cookie`
headers.

Example proxy: [public-demo.conf.example](public-demo.conf.example).

## Explicitly not exposed

- All owner API routes under `/api/v1/` other than the two exact
  public demo routes above (see [API reference](../api.md)).
- Web owner routes: `/api/backend/...`, `/api/session/...`,
  `/projects`, `/seeds`, `/experiments`, `/findings`, `/regressions`,
  `/mutations`, and every POST/PUT/PATCH/DELETE method publicly.
- No spendable operation, upload, export download, snapshot
  publish/revoke, provider key, or owner token is reachable publicly.

## Pre-publication checklist (root, in order)

1. Owner has published a snapshot with exact manifest hash, explicit
   intent, and redistribution acknowledgement; the current real
   snapshot is a private DRAFT with 0 findings, so this step is
   **not satisfied**.
2. License/owner choice recorded: MIT, `LICENSE` present
   (owner-confirmed 2026-10-30).
3. Secret/dependency/license/SBOM checks on the frozen revision
   recorded in the [evidence index](../releases/evidence-index.json).
4. Adapt the example config with a root-supplied hostname and TLS
   material; run the proxy's own config test; confirm the deny-all
   fallthrough and that owner routes return 404 publicly.
5. From a fresh unauthenticated session, verify `/demo`, the manifest,
   and one asset load, and that a private route and a POST fail.
   Record actual URLs and results; invent none beforehand.
6. Keep `OWNER_LABEL` stable; stop workers or disable providers
   during any restore/recovery near the demo host (recovered work
   can resume billing under LIVE workers).

## Rollback

Revoke the snapshot (`POST /api/v1/demo-snapshots/{id}/revoke`,
owner-only) or remove the proxy server block. Revocation fails
public reads closed; no data deletion is needed.

## Local rehearsal receipt

The example passed Nginx syntax validation and **45** local TLS route,
method and header assertions. [Receipt](../releases/proxy-rehearsal-results.json)
binds the config SHA-256 and image digest. Loopback-only disposable containers
and dummy upstreams were used, with an explicitly trusted test certificate.
This proves proxy policy locally; real product access and deployment remain pending.
