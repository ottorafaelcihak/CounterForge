#!/usr/bin/env bash
set -euo pipefail
if [[ "${RUN_META_LIVE_TESTS:-0}" != "1" ]]; then
  echo 'Live gate UNMET: RUN_META_LIVE_TESTS=1 is required.' >&2
  exit 2
fi
exec "${PYTHON:-python}" -m scripts.smoke_meta
