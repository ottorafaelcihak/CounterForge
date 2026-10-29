"""Fenced CPU-only export build; no database transaction spans ZIP construction."""

import asyncio
from typing import Any

from counterforge_regression.bundle import build_bundle
from counterforge_regression.runner_template import RUNNER_VERSION

from services.api.app.exports import collect_export, configured_secrets
from services.api.app.service import Service

from .jobs import fenced


async def execute_export(factory: Any, settings: Any, job: dict[str, Any]) -> None:
    with factory() as uow:
        if fenced(uow, job) is None:
            return
        snapshot = collect_export(Service(uow, settings), job["finding_id"], job["id"])
        uow.commit()
    forbidden = configured_secrets(settings)
    bundle = await asyncio.to_thread(
        build_bundle, snapshot.manifest, snapshot.evidence, forbidden_values=forbidden
    )
    with factory() as uow:
        if fenced(uow, job) is None:
            return
        s = Service(uow, settings)
        # Recheck current validity before registering any READY reference.
        current = collect_export(s, job["finding_id"], job["id"])
        if current != snapshot:
            raise ValueError("EXPORT_EVIDENCE_CHANGED")
        zip_ref = s.artifacts.put(
            job["project_id"], bundle.zip_bytes, "REGRESSION_ZIP", "application/zip"
        )
        manifest_ref = s.artifacts.put(
            job["project_id"], bundle.manifest_bytes, "MANIFEST", "application/json"
        )
        s.complete_export(
            job["id"],
            zip_ref.id,
            manifest_ref.id,
            RUNNER_VERSION,
            claim_token=job["claim_token"],
            delivery_no=job["delivery_no"],
        )
        uow.commit()
