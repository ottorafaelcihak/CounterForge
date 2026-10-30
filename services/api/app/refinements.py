"""Owner-scoped durable refinement intent; no provider I/O."""

from __future__ import annotations

import io
import json
from hashlib import sha256
from typing import Any
from uuid import UUID
from zipfile import ZipFile

from counterforge_contracts.requests import MinimizeRequest, RetestRequest
from counterforge_contracts.responses import JobRef
from counterforge_contracts.versions import ErrorCode
from counterforge_regression.bundle import verify_bundle
from counterforge_regression.manifest import RegressionManifest
from sqlalchemy import func, select

from .accounting import Budget
from .errors import ServiceError


def verified_export(s: Any, xid: UUID) -> tuple[Any, bytes, RegressionManifest]:
    ref = s.get_export(xid)
    if ref.state != "READY":
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Retest requires a current verified export.")
    row = s.row(s.t.regression_exports, xid)
    blob = s.artifacts.read_verified(row["bundle_asset_id"])
    verify_bundle(blob)
    with ZipFile(io.BytesIO(blob)) as bundle:
        manifest = RegressionManifest.model_validate_json(bundle.read("manifest.json"))
        image = bundle.read(manifest.files["failing"])
    if manifest.export_job_id != xid or manifest.finding_id != row["finding_id"]:
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Bundle identity differs.")
    return ref, image, manifest


def target_snapshot(row: Any) -> dict[str, Any]:
    return {
        k: row[k]
        for k in (
            "adapter_key",
            "model_id",
            "model_revision",
            "prompt_template",
            "inference_options",
        )
    }


def require_target(s: Any, tid: UUID, project_id: UUID) -> Any:
    target = s.target(tid)
    if target["project_id"] != project_id:
        raise ServiceError(ErrorCode.FORBIDDEN, "Retest target belongs to another project.", 403)
    if not target["enabled"] or target["model_id"] not in {
        "META_SPARK": {"muse-spark-1.3"},
        "LOCAL_FIXTURE": {"LOCAL_FIXTURE"},
    }.get(target["adapter_key"], set()):
        raise ServiceError(ErrorCode.INVALID_STATE, "Target must be enabled and curated.")
    return target


def require_retest_provenance(manifest: RegressionManifest, target: Any) -> None:
    if manifest.provenance == "LOCAL_FIXTURE" and target["adapter_key"] != "LOCAL_FIXTURE":
        raise ServiceError(
            ErrorCode.REVIEW_REQUIRED,
            "Simulated exports cannot become real-provider comparisons. Use a fixture target.",
        )


def request_retest(s: Any, xid: UUID, body: RetestRequest, key: str) -> JobRef:
    ref, image, manifest = verified_export(s, xid)
    f = s.finding(manifest.finding_id)
    e = s.experiment(f["experiment_id"])
    target = require_target(s, body.target_config_id, e["project_id"])
    require_retest_provenance(manifest, target)
    request = {
        **body.model_dump(mode="json"),
        "export_id": str(xid),
        "bundle_sha256": ref.bundle_sha256,
        "manifest_sha256": ref.manifest_sha256,
        "input_sha256": sha256(image).hexdigest(),
        "prompt_sha256": sha256(manifest.question.encode()).hexdigest(),
        "target_snapshot": target_snapshot(target),
        "manifest": json.loads(manifest.model_dump_json()),
    }
    jid = s.uow.background_jobs.enqueue(
        project_id=e["project_id"],
        kind="RETEST",
        finding_id=f["id"],
        request_json=request,
        idempotency_key=key,
    )
    return JobRef.model_validate(s.job_dto(s.job(jid)))


def require_source(s: Any, eid: UUID) -> Any:
    row = s.row(s.t.experiments, eid, lock=True)
    s.project(row["project_id"])
    Budget(s.uow).require_refinement_source(row)
    return row


def request_minimize(s: Any, fid: UUID, body: MinimizeRequest, key: str) -> JobRef:
    f = s.finding(fid)
    e = s.experiment(f["experiment_id"])
    m = s.candidates.row(f["mutation_id"])
    request = {
        **body.model_dump(mode="json"),
        "source_finding_id": str(fid),
        "source_mutation_id": str(m["id"]),
    }
    # Return a matching retained intent even after its source clock expires.
    previous = (
        s.db.execute(
            select(s.t.background_jobs).where(
                s.t.background_jobs.c.project_id == e["project_id"],
                s.t.background_jobs.c.kind == "MINIMIZE",
                s.t.background_jobs.c.idempotency_key == key,
            )
        )
        .mappings()
        .one_or_none()
    )
    if previous is not None:
        jid = s.uow.background_jobs.enqueue(
            project_id=e["project_id"],
            kind="MINIMIZE",
            experiment_id=e["id"],
            finding_id=fid,
            request_json=request,
            idempotency_key=key,
        )
        return JobRef.model_validate(s.job_dto(s.job(jid)))
    require_source(s, e["id"])
    if not s.finding_evidence(fid).export_eligible:
        raise ServiceError(
            ErrorCode.REVIEW_REQUIRED, "Minimization requires a currently eligible stable finding."
        )
    if e["candidates_created"] >= e["max_candidates"]:
        raise ServiceError(ErrorCode.BUDGET_EXCEEDED, "Source candidate cap is exhausted.")
    j = s.t.background_jobs
    active = s.db.scalar(
        select(j.c.id)
        .where(j.c.experiment_id == e["id"], j.c.kind == "MINIMIZE", j.c.finished_at.is_(None))
        .limit(1)
    )
    if active:
        raise ServiceError(ErrorCode.INVALID_STATE, "A refinement already owns this source.")
    jid = s.uow.background_jobs.enqueue(
        project_id=e["project_id"],
        kind="MINIMIZE",
        experiment_id=e["id"],
        finding_id=fid,
        request_json=request,
        idempotency_key=key,
    )
    return JobRef.model_validate(s.job_dto(s.job(jid)))


def resume_minimize(s: Any, jid: UUID) -> JobRef:
    job = s.job(jid)
    require_source(s, job["experiment_id"]) if job["kind"] == "MINIMIZE" else None
    job = s.row(s.t.background_jobs, jid, lock=True)
    cp = job["checkpoint_json"]
    if (
        job["kind"] != "MINIMIZE"
        or job["state"] != "REVIEW_REQUIRED"
        or job["finished_at"] is not None
        or not cp.get("awaiting_review")
    ):
        raise ServiceError(
            ErrorCode.INVALID_STATE, "Only an independently reviewed minimization probe can resume."
        )
    mid = UUID(cp["probes"][-1]["mutation_id"])
    m = s.candidates.row(mid)
    if m["minimization_job_id"] != jid:
        raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Probe lineage differs.")
    report = s.candidates.checked_report(m)
    if report.verdict == "INVALID" and report.review_audit is not None:
        pass  # Record the independently rejected probe without any dispatch.
    else:
        s.require_valid_candidate(mid)
    u = s.t.usage_events
    if s.db.scalar(
        select(u.c.id)
        .where(u.c.background_job_id == jid, u.c.status.in_(["RESERVED", "UNKNOWN_CHARGE"]))
        .limit(1)
    ):
        raise ServiceError(
            ErrorCode.REVIEW_REQUIRED, "Resolve retained provider work before resume."
        )
    # Queue admission also applies to a followup returning from human review.
    s.db.execute(select(func.pg_advisory_xact_lock(73402031)))
    pending = (
        s.db.scalar(
            select(func.count())
            .select_from(s.t.background_jobs)
            .where(
                s.t.background_jobs.c.state.in_(["RUNNING", "QUEUED"]),
                (s.t.background_jobs.c.experiment_id.is_(None))
                | (s.t.background_jobs.c.kind == "MINIMIZE"),
            )
        )
        or 0
    )
    pending += (
        s.db.scalar(
            select(func.count())
            .select_from(s.t.experiments)
            .where(s.t.experiments.c.dispatch_pending | s.t.experiments.c.runner_token.is_not(None))
        )
        or 0
    )
    if pending >= s.settings.max_pending_jobs:
        raise ServiceError(ErrorCode.QUEUE_FULL, "Queue is at capacity.", 429)
    s.db.execute(
        s.t.background_jobs.update()
        .where(s.t.background_jobs.c.id == jid)
        .values(
            state="QUEUED",
            claim_token=None,
            lease_expires_at=None,
            error_code=None,
            checkpoint_json={**cp, "awaiting_review": False},
            updated_at=func.now(),
        )
    )
    s.uow.background_jobs.new_job_ids.append(jid)
    return JobRef.model_validate(s.job_dto(s.job(jid)))


def cancel_minimize(s: Any, jid: UUID) -> JobRef:
    job = s.row(s.t.background_jobs, jid, lock=True)
    s.project(job["project_id"])
    if (
        job["kind"] != "MINIMIZE"
        or job["state"] != "REVIEW_REQUIRED"
        or job["finished_at"] is not None
    ):
        raise ServiceError(ErrorCode.INVALID_STATE, "Only a paused minimization can be stopped.")
    result = dict(job["result_json"])
    result["outcome"] = "COMPLETED"
    result["minimization"] = {**result["minimization"], "stop_reason": "OWNER_STOP"}
    s.db.execute(
        s.t.background_jobs.update()
        .where(s.t.background_jobs.c.id == jid)
        .values(
            state="SUCCEEDED",
            result_json=result,
            finished_at=func.now(),
            updated_at=func.now(),
            claim_token=None,
            lease_expires_at=None,
            error_code=None,
        )
    )
    return JobRef.model_validate(s.job_dto(s.job(jid)))
