"""Bounded pre-experiment SCENE and supporting candidate SAM jobs.

Financial operations use the Phase03 Accounting port. Every evidence write and
new validation revision is performed in the final transaction while fenced.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.requests import JobLimits
from counterforge_contracts.responses import JobResult
from counterforge_contracts.versions import ErrorCode
from counterforge_meta.image import SceneGenerator
from counterforge_meta.sam import SamSegmenter
from counterforge_meta.transport import MetaTransport
from counterforge_meta.usage import PriceBook
from counterforge_mutations.registry import approve_plan
from counterforge_verification.invariants import HUMAN_CODES, verify_candidate
from sqlalchemy import func, select

from services.api.app.artifacts import normalize_image
from services.api.app.errors import ServiceError
from services.api.app.masks import BinaryMask, decode_mask
from services.api.app.provider_accounting import Accounting
from services.api.app.service import Service, canonical_json, fingerprint

from .fixtures import mock_transport, require_seed


def media_type(body: bytes) -> str:
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("INVALID_MEDIA")


async def execute_preparation(factory: Any, settings: Any, job: dict[str, Any]) -> None:
    from .jobs import fenced

    request = job["request_json"]
    limits = JobLimits.model_validate(request["limits"])
    synthetic = settings.provider_mode == "FIXTURE"
    if settings.provider_mode == "DISABLED":
        raise ValueError("PROVIDERS_DISABLED")
    with factory() as uow:
        s = Service(uow, settings)
        now = uow.connection.scalar(select(func.clock_timestamp()))
        remaining = limits.max_runtime_seconds - (now - job["started_at"]).total_seconds()
        if remaining <= 0:
            raise ServiceError(ErrorCode.BUDGET_EXCEEDED, "Job runtime limit was reached.")
        if job["experiment_id"] is not None:
            raise ValueError("PREPARATORY_JOB_REQUIRED")
        s.project(job["project_id"])
        if job["kind"] == "SCENE":
            snapshot = request["snapshot"]
            if fingerprint(snapshot) != request["snapshot_sha256"]:
                raise ValueError("SNAPSHOT_CHANGED")
            contract, source, _ = s.candidates.snapshot(
                UUID(request["contract_id"]), snapshot, job["project_id"]
            )
            approved = approve_plan(
                contract, MutationPlan.model_validate_json(request["plan_json"])
            )
            plan = MutationPlan.model_validate_json(approved.plan_json)
            if (
                not plan.requires_muse_image
                or plan.target_region != "background_only"
                or plan.family == "brightness_shift"
                or approved.plan_json != request["plan_json"]
                or source.sha256 != request["source_sha256"]
            ):
                raise ValueError("INVALID_BACKGROUND_PLAN")
            image = source.png
            source_asset = s.asset(source.asset_id)
            width, height = source_asset["width"], source_asset["height"]
            instruction = (
                s.candidates.clean(plan.prompt)
                + "\nChange background only. Preserve protected "
                + s.candidates.clean(contract.protected.label)
                + ". Do not add, remove, repaint or occlude any target-class object."
            )
        else:
            row = s.candidates.row(UUID(request["mutation_id"]))
            experiment = s.experiment(row["experiment_id"])
            if (
                experiment["project_id"] != job["project_id"]
                or request["source_sha256"] != row["evidence_json"]["asset_hashes"]["candidate"]
            ):
                raise ValueError("CANDIDATE_BINDING_FAILED")
            s.candidates.checked_report(row)
            contract, source, generated, _ = s.candidates.retained(row)
            image = generated.png
            width, height = generated.recipe.width, generated.recipe.height
            instruction = s.candidates.clean(contract.protected.label)
        expected = contract.expected.value
    if synthetic:
        # Candidate SAM may register only the exact retained candidate derived
        # from the original development fixture. Other fixture gates stay strict.
        require_seed(source.png)
    accounting = Accounting(
        factory,
        settings,
        job_id=job["id"],
        claim_token=job["claim_token"],
        delivery=job["delivery_no"],
        purpose="IMAGE" if job["kind"] == "SCENE" else "SAM",
        synthetic=synthetic,
    )
    async with asyncio.timeout(remaining):
        async with MetaTransport(
            api_key="synthetic-fixture-key"
            if synthetic
            else settings.model_api_key.get_secret_value(),
            accounting=accounting,
            prices=PriceBook.load(settings.pricing_path),
            http_transport=mock_transport(
                sam_candidate_body=image if job["kind"] == "MUTATION_SAM" else None
            )
            if synthetic
            else None,
            deadline_seconds=min(45.0, remaining),
        ) as transport:
            if job["kind"] == "SCENE":
                raw = await SceneGenerator(transport=transport).create_scene(image, instruction)
                normalized = await asyncio.to_thread(
                    normalize_image,
                    raw,
                    media_type(raw),
                    strict_opaque=True,
                    expected_size=(width, height),
                )
                with factory() as uow:
                    if fenced(uow, job) is None:
                        return
                    s = Service(uow, settings)
                    # Frozen bytes checked again before any completion writes.
                    s.candidates.snapshot(
                        UUID(request["contract_id"]), request["snapshot"], job["project_id"]
                    )
                    meta = {
                        "source_job_id": str(job["id"]),
                        "source_sha256": request["source_sha256"],
                        "synthetic": synthetic,
                        "preparatory_usage": True,
                    }
                    original = s.artifacts.put(
                        job["project_id"],
                        raw,
                        "OTHER",
                        media_type(raw),
                        width=normalized.original_width,
                        height=normalized.original_height,
                        metadata=meta,
                    )
                    output = s.artifacts.put(
                        job["project_id"],
                        normalized.png,
                        "OTHER",
                        "image/png",
                        width=width,
                        height=height,
                        metadata={
                            **meta,
                            "normalization_policy": "strict-opaque-" + normalized.policy,
                        },
                    )
                    result = JobResult(
                        outcome="COMPLETED",
                        synthetic=synthetic,
                        background_asset_id=output.id,
                        background_sha256=output.sha256,
                        original_background_asset_id=original.id,
                        original_background_sha256=original.sha256,
                        evidence_asset_ids=[original.id, output.id],
                    )
                    complete(s, job, result)
                    uow.commit()
            else:
                proposals: dict[UUID, BinaryMask] = {}

                async def writer(object_id: str, data: bytes) -> UUID:
                    mask = await asyncio.to_thread(decode_mask, data, width=width, height=height)
                    aid = uuid4()
                    proposals[aid] = mask
                    return aid

                segmented = await SamSegmenter(transport=transport, mask_writer=writer).segment(
                    image, instruction
                )
                with factory() as uow:
                    if fenced(uow, job) is None:
                        return
                    s = Service(uow, settings)
                    row = s.candidates.row(UUID(request["mutation_id"]), lock=True)
                    if (
                        row["evidence_json"]["asset_hashes"]["candidate"]
                        != request["source_sha256"]
                    ):
                        raise ValueError("CANDIDATE_CHANGED")
                    prior_report = s.candidates.checked_report(row)
                    contract, source, generated, policy = s.candidates.retained(row)
                    masks = []
                    objects = []
                    for obj in segmented.objects:
                        mask = proposals[obj.mask_asset_id]
                        a = s.artifacts.put(
                            job["project_id"],
                            mask.png,
                            "MASK",
                            "image/png",
                            width=width,
                            height=height,
                            metadata={
                                "source_job_id": str(job["id"]),
                                "candidate_id": str(row["id"]),
                                "synthetic": synthetic,
                            },
                        )
                        masks.append({"asset_id": str(a.id), "sha256": a.sha256})
                        objects.append(
                            {
                                **obj.model_dump(mode="json"),
                                "mask_asset_id": str(a.id),
                                "geometry": mask.geometry(),
                            }
                        )
                    document = {
                        "job_id": str(job["id"]),
                        "mutation_id": str(row["id"]),
                        "source_sha256": request["source_sha256"],
                        "outcome": segmented.outcome,
                        "count": len(segmented.objects),
                        "objects": objects,
                        "diagnostics": list(segmented.diagnostics),
                        "synthetic": synthetic,
                    }
                    evidence = s.artifacts.put(
                        job["project_id"],
                        canonical_json(document),
                        "OTHER",
                        "application/json",
                        metadata={"synthetic": synthetic},
                    )
                    sam = {
                        "asset_id": str(evidence.id),
                        "sha256": evidence.sha256,
                        "outcome": segmented.outcome,
                        "count": len(segmented.objects),
                        "masks": masks,
                    }
                    # Recompute from raster evidence: no prior human approval survives
                    # a new observation, including matching detector count.
                    ids = (
                        tuple(
                            dict.fromkeys(
                                [
                                    *(UUID(a) for a in row["evidence_json"]["assets"].values()),
                                    *(m.asset_id for m in generated.reviewed_masks),
                                ]
                            )
                        )
                        + (evidence.id,)
                        + tuple(UUID(m["asset_id"]) for m in masks)
                    )
                    policy = replace(
                        policy,
                        sam_outcome=segmented.outcome,
                        sam_count=len(segmented.objects),
                        sam_evidence_sha256=evidence.sha256,
                        evidence_asset_ids=ids,
                    )
                    report = verify_candidate(contract, source, generated, policy)
                    # Same immutable image: supporting observations invalidate
                    # approvals, but cannot erase a confirmed human violation.
                    # Read the latest sealed revision under the mutation lock so
                    # an in-flight review cannot be lost at completion. The new
                    # revision's predecessor retains the exact prior assertion.
                    failures = {
                        check.code: check.model_copy(deep=True)
                        for check in prior_report.checks
                        if check.code in HUMAN_CODES and check.mandatory and check.status == "FAIL"
                    }
                    report.checks = [failures.get(check.code, check) for check in report.checks]
                    if failures:
                        report.confidence_note += " Prior confirmed human failures remain until an explicit evidence-bound audit resolves them."
                    s.candidates.append(row, report, sam=sam)
                    result = JobResult(
                        outcome=segmented.outcome,
                        synthetic=synthetic,
                        detected_count=len(segmented.objects),
                        expected_count=expected,
                        mask_asset_ids=[UUID(m["asset_id"]) for m in masks],
                        diagnostics=list(segmented.diagnostics),
                        evidence_asset_ids=[evidence.id],
                    )
                    complete(s, job, result)
                    uow.commit()


def complete(service: Service, job: dict[str, Any], result: JobResult) -> None:
    service.db.execute(
        service.t.background_jobs.update()
        .where(service.t.background_jobs.c.id == job["id"])
        .values(
            state="SUCCEEDED" if result.outcome == "COMPLETED" else "REVIEW_REQUIRED",
            result_json=result.model_dump(mode="json"),
            finished_at=func.now(),
            updated_at=func.now(),
            lease_expires_at=None,
            error_code=None if result.outcome == "COMPLETED" else "REVIEW_REQUIRED",
        )
    )
    service.audit(
        job["project_id"],
        "PREPARATION_COMPLETED",
        "job",
        job["id"],
        kind=job["kind"],
        synthetic=result.synthetic,
        preparatory_usage=True,
    )
