"""Durable implementation of the adapter accounting port; no transaction spans I/O."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict
from decimal import Decimal
from typing import Any
from uuid import UUID

from counterforge_contracts.providers import ProviderResult
from counterforge_contracts.versions import ErrorCode
from counterforge_meta.usage import CallIntent, Charge, Outcome, sanitize_raw, semantic_digest
from pydantic import JsonValue
from sqlalchemy import func, select
from sqlalchemy.engine import Connection

from .accounting import Budget, ProviderUsage
from .artifacts import ArtifactStore
from .config import Settings
from .db.uow import UnitOfWork
from .errors import ServiceError

PROVIDER_LOCK = 73402032


class Accounting:
    def __init__(
        self,
        factory: Callable[[], UnitOfWork],
        settings: Settings,
        *,
        job_id: UUID,
        claim_token: UUID,
        delivery: int,
        purpose: str,
        synthetic: bool = False,
    ) -> None:
        if not re.fullmatch(r"[A-Z_0-9]{1,30}", purpose):
            raise ValueError("Invalid operation purpose")
        if synthetic != (settings.provider_mode == "FIXTURE"):
            raise ValueError("Provider accounting mode mismatch")
        self.factory, self.settings = factory, settings
        self.job_id, self.claim_token, self.delivery = job_id, claim_token, delivery
        self.purpose, self.synthetic = purpose, synthetic

    def owned_job(self, uow: UnitOfWork, *, fence: bool = True) -> Any:
        jobs, projects = uow.tables.background_jobs, uow.tables.projects
        job = (
            uow.connection.execute(select(jobs).where(jobs.c.id == self.job_id).with_for_update())
            .mappings()
            .one()
        )
        owner = uow.connection.scalar(
            select(projects.c.owner_label).where(projects.c.id == job["project_id"])
        )
        if owner != self.settings.owner_label:
            raise ServiceError(ErrorCode.FORBIDDEN, "Job is outside the configured owner.", 403)
        if fence and (
            job["claim_token"] != self.claim_token
            or job["delivery_no"] != self.delivery
            or job["state"] != "RUNNING"
            or job["lease_expires_at"] is None
            or job["lease_expires_at"] <= uow.connection.scalar(select(func.now()))
        ):
            raise ServiceError(ErrorCode.INVALID_STATE, "Job dispatch fence is no longer owned.")
        return job

    async def reserve(self, intent: CallIntent) -> Ticket:
        key = f"{self.job_id}:{self.delivery}:{self.purpose}:{intent.attempt}"
        with self.factory() as uow:
            job = self.owned_job(uow)
            if self.settings.provider_mode == "DISABLED":
                raise ServiceError(ErrorCode.INVALID_STATE, "Providers are disabled.")
            if job["experiment_id"] is None:
                reservation = Budget(uow).reserve_job_operation(
                    self.job_id,
                    key,
                    intent.category,
                    Decimal(0) if self.synthetic else intent.upper_bound_usd,
                    provider="FIXTURE" if self.synthetic else "META",
                    model_id=intent.model,
                    semantic_sha256=intent.semantic_sha256,
                )
            else:
                reservation = Budget(uow).reserve_operation(
                    job["experiment_id"],
                    key,
                    intent.category,
                    Decimal(0) if self.synthetic else intent.upper_bound_usd,
                    provider="FIXTURE" if self.synthetic else "META",
                    model_id=intent.model,
                    semantic_sha256=intent.semantic_sha256,
                )
                usage_table = uow.tables.usage_events
                uow.connection.execute(
                    usage_table.update()
                    .where(usage_table.c.operation_key == key)
                    .values(background_job_id=self.job_id)
                )
            usage = uow.tables.usage_events
            prior = (
                uow.connection.execute(select(usage).where(usage.c.operation_key == key))
                .mappings()
                .one()
            )
            if prior["pricing_revision"] not in (None, intent.pricing_revision) or (
                not reservation.created and prior["attempt_no"] != intent.attempt
            ):
                raise ServiceError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Operation pricing or attempt cannot change."
                )
            uow.connection.execute(
                usage.update()
                .where(usage.c.operation_key == key)
                .values(pricing_revision=intent.pricing_revision, attempt_no=intent.attempt)
            )
            uow.commit()
        return Ticket(self, key, intent)


class Ticket:
    def __init__(self, accounting: Accounting, key: str, intent: CallIntent) -> None:
        self.accounting, self.key, self.intent = accounting, key, intent
        self.lease: Connection | None = None

    def release(self) -> None:
        if self.lease is not None:
            connection, self.lease = self.lease, None
            try:
                connection.execute(select(func.pg_advisory_unlock(PROVIDER_LOCK)))
                connection.commit()
            except Exception:
                # A session advisory lock survives pool rollback. A failed unlock
                # must discard the physical connection instead of pooling it.
                connection.invalidate()
            finally:
                connection.close()

    async def claim(self) -> bool:
        a = self.accounting
        with a.factory() as uow:
            # A dedicated session lock survives commits but holds no SQL transaction.
            lease = uow.engine.connect()
            acquired = lease.scalar(select(func.pg_try_advisory_lock(PROVIDER_LOCK)))
            lease.commit()
            if not acquired:
                lease.close()
                Budget(uow).void_operation(self.key)
                uow.commit()
                return False
            self.lease = lease
            try:
                a.owned_job(uow)
                claimed = Budget(uow).begin_operation(self.key)
                uow.commit()
                if not claimed:
                    self.release()
                return claimed
            except BaseException:
                self.release()
                # A failed fence/runtime check must not leave an undispatched hold.
                uow.rollback()
                with a.factory() as cleanup:
                    usage = cleanup.tables.usage_events
                    row = (
                        cleanup.connection.execute(
                            select(usage).where(usage.c.operation_key == self.key)
                        )
                        .mappings()
                        .one()
                    )
                    if row["status"] == "RESERVED" and row["dispatched_at"] is None:
                        Budget(cleanup).void_operation(self.key)
                    cleanup.commit()
                raise

    async def finish(
        self,
        outcome: Outcome,
        charge: Charge,
        metadata: dict[str, JsonValue],
        sanitized_raw: dict[str, JsonValue],
    ) -> ProviderResult:
        a = self.accounting
        secret = a.settings.model_api_key.get_secret_value()
        raw = sanitize_raw(sanitized_raw, secrets=(secret,))
        # Metadata is an allowlist, never an arbitrary worker/provider object.
        safe: dict[str, Any] = {}
        for key in ("latency_ms", "request_id", "usage", "warnings"):
            value = metadata.get(key)
            if key == "latency_ms":
                safe[key] = value if type(value) is int and 0 <= value <= 10**9 else 0
            elif key == "request_id":
                safe[key] = (
                    value
                    if isinstance(value, str)
                    and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value)
                    and not (secret and secret in value)
                    else None
                )
            elif key == "usage":
                safe[key] = (
                    (sanitize_raw({"usage": value}, secrets=(secret,)).get("usage") or {})
                    if isinstance(value, dict)
                    else {}
                )
            elif key == "warnings":
                safe[key] = (
                    [
                        v
                        for v in value
                        if isinstance(v, str)
                        and re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", v)
                        and not (secret and secret in v)
                    ]
                    if isinstance(value, list)
                    else []
                )
        if a.synthetic:
            charge = Charge("ACTUAL", Decimal(0), charge.request_sent, "VERIFIED_USAGE")
        signature = semantic_digest(
            {
                "outcome": outcome,
                "charge": {**asdict(charge), "amount_usd": str(charge.amount_usd)},
                "metadata": safe,
                "raw": raw,
            }
        )
        try:
            with a.factory() as uow:
                job = a.owned_job(uow, fence=False)
                table = uow.tables.usage_events
                row = (
                    uow.connection.execute(
                        select(table).where(table.c.operation_key == self.key).with_for_update()
                    )
                    .mappings()
                    .one()
                )
                prior = row["metadata_json"].get("finish_sha256")
                if prior is not None:
                    if prior != signature:
                        raise ServiceError(
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "Finished provider evidence cannot change.",
                        )
                    asset_id = row["raw_response_asset_id"]
                else:
                    if row["dispatched_at"] is None:
                        raise ServiceError(
                            ErrorCode.INVALID_STATE, "Unclaimed provider attempt cannot finish."
                        )
                    artifact = ArtifactStore(uow, a.settings.artifact_root).put(
                        job["project_id"],
                        json.dumps(
                            raw, sort_keys=True, separators=(",", ":"), allow_nan=False
                        ).encode(),
                        "RAW_RESPONSE",
                        "application/json",
                        metadata={
                            "synthetic": a.synthetic,
                            "semantic_sha256": self.intent.semantic_sha256,
                            "pricing_revision": self.intent.pricing_revision,
                            "outcome": outcome,
                            "attempt": self.intent.attempt,
                            "operation_key": self.key,
                        },
                    )
                    asset_id = artifact.id
                    budget = Budget(uow)
                    if charge.kind == "ACTUAL":
                        usage = safe.get("usage", {})
                        budget.settle_operation(
                            self.key,
                            ProviderUsage(
                                charge.amount_usd,
                                tokens_in=usage.get("prompt_tokens"),
                                tokens_out=usage.get("completion_tokens"),
                                latency_ms=safe.get("latency_ms"),
                                provider_request_id=safe.get("request_id"),
                            ),
                        )
                    elif charge.kind == "ESTIMATED":
                        budget.settle_estimate(self.key)
                    elif charge.kind == "UNKNOWN":
                        budget.mark_unknown_charge(self.key, charge.reason)
                    else:
                        budget.finish_unbilled(
                            self.key, proof_code=charge.reason, request_sent=charge.request_sent
                        )
                    # Re-read because Budget adds its durable nonactual_proof.
                    existing = uow.connection.scalar(
                        select(table.c.metadata_json).where(table.c.id == row["id"])
                    )
                    uow.connection.execute(
                        table.update()
                        .where(table.c.id == row["id"])
                        .values(
                            outcome=outcome,
                            raw_response_asset_id=asset_id,
                            metadata_json={
                                **(existing or {}),
                                **safe,
                                "finish_sha256": signature,
                                "charge_reason": charge.reason,
                                "synthetic": a.synthetic,
                            },
                        )
                    )
                uow.commit()
            return ProviderResult(
                provider="FIXTURE" if a.synthetic else "META",
                model=self.intent.model,
                raw_asset_key=str(asset_id),
                request_id=safe.get("request_id"),
                latency_ms=safe.get("latency_ms", 0),
                usage=safe.get("usage", {}),
                warnings=tuple(safe.get("warnings", [])),
            )
        finally:
            self.release()


class ExperimentAccounting(Accounting):
    """Same execution port and Ticket settlement, attributed to an experiment."""

    def __init__(
        self,
        factory: Callable[[], UnitOfWork],
        settings: Settings,
        *,
        experiment_id: UUID,
        token: UUID,
        delivery: int,
        purpose: str,
        mutation_id: UUID | None = None,
        invocation_sha256: str | None = None,
    ) -> None:
        super().__init__(
            factory,
            settings,
            job_id=experiment_id,
            claim_token=token,
            delivery=delivery,
            purpose=purpose,
            synthetic=settings.provider_mode == "FIXTURE",
        )
        self.mutation_id = mutation_id
        self.invocation_sha256 = invocation_sha256

    def owned_job(self, uow: UnitOfWork, *, fence: bool = True) -> Any:
        from .service import Service

        s = Service(uow, self.settings)
        row = s.row(uow.tables.experiments, self.job_id, lock=True)
        s.project(row["project_id"])
        if fence:
            from .experiments import require_provider_mode

            require_provider_mode(s, row)
            if (
                row["runner_token"] != self.claim_token
                or row["delivery_no"] != self.delivery
                or row["lease_expires_at"] is None
                or row["lease_expires_at"] <= uow.connection.scalar(select(func.clock_timestamp()))
            ):
                raise ServiceError(ErrorCode.INVALID_STATE, "Experiment runner fence expired.")
            Budget(uow)._require_runnable(row)
            if self.mutation_id is not None:
                candidate = s.require_valid_candidate(self.mutation_id)
                if candidate.experiment_id != self.job_id:
                    raise ServiceError(ErrorCode.FORBIDDEN, "Candidate experiment mismatch.", 403)
        return row

    async def reserve(self, intent: CallIntent) -> Ticket:
        key = f"{self.job_id}:{self.purpose}:{self.mutation_id or 'seed'}:{intent.attempt}"
        with self.factory() as uow:
            self.owned_job(uow)
            # Only a provably unsent VOID slot can acquire a fresh reservation.
            base_key = key
            suffix = 0
            usage_table = uow.tables.usage_events
            while True:
                prior = (
                    uow.connection.execute(
                        select(usage_table).where(usage_table.c.operation_key == key)
                    )
                    .mappings()
                    .one_or_none()
                )
                if prior is None or not (
                    prior["status"] == "VOID" and prior["dispatched_at"] is None
                ):
                    break
                suffix += 1
                key = f"{base_key}:safe{suffix}"
            if (
                prior is not None
                and self.invocation_sha256 is not None
                and (prior["metadata_json"].get("invocation_sha256") != self.invocation_sha256)
            ):
                raise ServiceError(ErrorCode.REVIEW_REQUIRED, "Retained target invocation differs.")
            if self.settings.provider_mode == "DISABLED":
                raise ServiceError(ErrorCode.INVALID_STATE, "Providers are disabled.")
            # Convert one slot of the atomic confirmation hold to the real attempt.
            # Parent lock is still held, so another reservation cannot steal a slot.
            from .service import Service

            s = Service(uow, self.settings)
            experiment = s.experiment(self.job_id)
            batch = experiment["checkpoint_json"].get("confirmation_batch")
            remainder = None
            if self.purpose.startswith("CONFIRMATION_") and batch and prior is None:
                Budget(uow).void_operation(batch["key"])
                remainder = batch["remaining"] - 1
            Budget(uow).reserve_operation(
                self.job_id,
                key,
                intent.category,
                Decimal(0) if self.synthetic else intent.upper_bound_usd,
                target_calls=1 if intent.category == "TARGET" else 0,
                provider="FIXTURE" if self.synthetic else "META",
                model_id=intent.model,
                semantic_sha256=intent.semantic_sha256,
            )
            if remainder is not None:
                next_batch = None
                if remainder:
                    next_key = batch["key"] + f":r{remainder}"
                    Budget(uow).reserve_operation(
                        self.job_id,
                        next_key,
                        "TARGET",
                        Decimal(batch["per_usd"]) * remainder,
                        target_calls=remainder,
                        provider="BATCH_HOLD",
                        model_id=intent.model,
                    )
                    next_batch = {**batch, "key": next_key, "remaining": remainder}
                uow.connection.execute(
                    uow.tables.experiments.update()
                    .where(uow.tables.experiments.c.id == self.job_id)
                    .values(
                        checkpoint_json={
                            **experiment["checkpoint_json"],
                            "confirmation_batch": next_batch,
                        }
                    )
                )
            usage = uow.tables.usage_events
            uow.connection.execute(
                usage.update()
                .where(usage.c.operation_key == key)
                .values(pricing_revision=intent.pricing_revision, attempt_no=intent.attempt)
            )
            if self.invocation_sha256 is not None:
                metadata = uow.connection.scalar(
                    select(usage.c.metadata_json).where(usage.c.operation_key == key)
                )
                uow.connection.execute(
                    usage.update()
                    .where(usage.c.operation_key == key)
                    .values(
                        metadata_json={
                            **(metadata or {}),
                            "invocation_sha256": self.invocation_sha256,
                        }
                    )
                )
            uow.commit()
        return Ticket(self, key, intent)
