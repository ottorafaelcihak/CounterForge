# Security (operational notes)

- All published ports bind to `127.0.0.1`. Remote access stays behind
  SSH/Tailscale; no service is exposed externally by these changes.
- Owner credentials travel only in the `Authorization` header, never in
  URLs, Compose healthcheck commands, backup manifests, or latency-tool
  arguments. The backup CLI passes database passwords to pg tools only
  through a minimal subprocess environment (PATH + PGPASSWORD), never in
  argv or stdout, and never uses a shell.
- Readiness responses contain booleans only: no service URLs, filesystem
  paths, exception text, or provider state. Health probes never call a
  provider and never echo request content; API validation errors are
  sanitized envelopes with correlation IDs.
- Ordinary logs are bounded per service (10 MB × 3, json-file). Do not log
  request bodies, prompts, provider responses, or credentials; existing
  error handling persists diagnostic codes rather than exception text.
- Backups contain evidence and database contents: store them with 0600
  files / 0700 directories (the CLI does this), treat them as sensitive,
  and restore only into empty, disposable destinations. Restore rejects
  traversal, symlinks, duplicate paths, hash mismatches, and unknown
  manifest schemas before changing anything. PostgreSQL dumps are trusted
  operator data, not a format for accepting untrusted uploads.
- Retention never deletes referenced or active evidence. Backup pruning
  and artifact orphan maintenance are manual, dry-run by default, and
  refuse unverifiable data.
- The public demo remains a whitelisted snapshot; no operational tool
  publishes it or grants anonymous access to private assets.
