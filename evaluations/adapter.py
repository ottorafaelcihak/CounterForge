"""Local Service/worker adapter, no alternate provider client or synthetic live path."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from counterforge_contracts.requests import StartExperiment
from counterforge_contracts.vtc import Limits
from sqlalchemy import select

from services.api.app.experiments import transition
from services.api.app.service import Service, fingerprint
from services.worker.app.experiments import execute_experiment

from .dataset import DatasetManifest
from .integrity import json_default
from .protocol import LiveAuthorization, Protocol, validate_protocol

TARGET_KEYS = (
    "adapter_key",
    "provider",
    "model_id",
    "model_revision",
    "prompt_template",
    "inference_options",
)


class ExistingExperimentAdapter:
    def __init__(
        self,
        factory: Any,
        settings: Any,
        project_id: UUID,
        bindings: dict[str, dict[str, str]],
        protocol: Protocol,
        *,
        dataset: DatasetManifest | None = None,
        authorization: LiveAuthorization | None = None,
    ) -> None:
        validate_protocol(protocol)
        if settings.enqueue_jobs:
            raise ValueError(
                "Evaluation uses serial local deliveries; queue dispatch must be disabled"
            )
        if settings.provider_mode not in {"FIXTURE", "LIVE"}:
            raise ValueError("Evaluation requires explicit fixture or live deployment")
        if settings.provider_mode == "LIVE":
            if (
                authorization is None
                or dataset is None
                or dataset.sha256 != protocol.dataset_sha256
            ):
                raise ValueError(
                    "LIVE requires a separate explicit authorization and approved frozen dataset"
                )
            from .integrity import verify

            verify(dataset.model_dump(mode="json"))
            authorization.check(protocol, datetime.now(UTC))
        self.factory, self.settings, self.project_id = factory, settings, project_id
        self.bindings, self.protocol, self.dataset, self.authorization = (
            bindings,
            protocol,
            dataset,
            authorization,
        )

    def prepare(self, intents: list[dict[str, Any]], started_at: str) -> None:
        groups: dict[str, list[str]] = {}
        self.ids_by_key: dict[str, str] = {}
        for intent in intents:
            eid = str(uuid5(NAMESPACE_URL, "counterforge-evaluation:" + intent["key"]))
            self.ids_by_key[intent["key"]] = eid
            groups.setdefault(intent["case"]["case_id"], []).append(eid)
        declaration = dict(
            protocol_sha256=self.protocol.sha256,
            dataset_sha256=self.protocol.dataset_sha256,
            policy_revision=self.protocol.policy_revision,
            target_revision=self.protocol.target_revision,
            experiment_ids=list(self.ids_by_key.values()),
            baseline_groups=list(groups.values()),
            started_at=started_at,
            max_api_calls=self.protocol.max_api_calls,
            max_usd=str(self.protocol.max_usd),
            max_runtime_seconds=self.protocol.max_runtime_seconds,
            authorization_expires_at=self.authorization.expires_at.isoformat()
            if self.authorization
            else None,
            authorization_sha256=self.authorization.sha256 if self.authorization else None,
        )
        declaration["run_sha256"] = fingerprint(declaration)
        declaration["sha256"] = fingerprint(declaration)
        self.declaration = declaration

    def create(self, key: str, case: dict[str, Any], arm: str, protocol: Protocol) -> str:
        if protocol != self.protocol or arm not in protocol.arms:
            raise ValueError("Changed immutable declaration")
        binding = self.bindings[case["case_id"]]
        with self.factory() as uow:
            s = Service(uow, self.settings)
            if self.authorization is not None and hasattr(self, "declaration"):
                # One authorization binds exactly one run in this database.
                from sqlalchemy import select as _select

                peers = list(
                    s.db.execute(
                        _select(s.t.experiments.c.config_snapshot).where(
                            s.t.experiments.c.config_snapshot["evaluation"][
                                "authorization_sha256"
                            ].astext
                            == self.authorization.sha256
                        )
                    ).scalars()
                )
                if any(
                    (p or {}).get("evaluation", {}).get("run_sha256")
                    != self.declaration["run_sha256"]
                    for p in peers
                ):
                    raise ValueError(
                        "Live authorization already bound to a different evaluation run"
                    )
            cid = UUID(
                binding.get("full_contract_id", binding["contract_id"])
                if arm == "full_system"
                else binding["contract_id"]
            )
            contract = s.contract(cid)
            target = s.target(UUID(binding["target_config_id"]))
            if fingerprint({k: target[k] for k in TARGET_KEYS}) != protocol.target_revision:
                raise ValueError("Target revision differs from frozen declaration")
            families = (
                protocol.full_families if arm == "full_system" else protocol.deterministic_families
            )
            if tuple(contract["contract_json"]["allowed_mutations"]) != families:
                raise ValueError(
                    "Contract families differ from declared deterministic/generator ablation"
                )
            if contract["contract_json"]["reproduction"] != {"attempts": 3, "required_failures": 3}:
                raise ValueError("Fresh confirmation policy differs")
            if self.dataset:
                entry = next(e for e in self.dataset.entries if e.case_id == case["case_id"])
                seed = s.seed(contract["seed_id"])
                provenance = seed["provenance_json"]
                if (
                    entry.split != case["split"]
                    or provenance["original_sha256"] != entry.source_sha256
                    or provenance["normalized_sha256"] != entry.normalized_sha256
                    or seed["expected_json"]["value"] != entry.verified_count
                    or seed["label_reviewer"] != entry.label_reviewer
                ):
                    raise ValueError("Imported seed differs from frozen human evidence")
            # Each arm may reserve up to the global dollars; only the serialized
            # aggregate guard grants spending rights. This avoids tiny partitions.
            limits = Limits(
                max_candidates=protocol.max_candidates_per_arm,
                max_target_calls=protocol.target_calls_per_arm,
                max_estimated_usd=float(max(protocol.max_usd, Decimal("0.000001"))),
                max_runtime_seconds=protocol.max_runtime_seconds,
            )
            if not hasattr(self, "declaration") or key not in self.ids_by_key:
                raise ValueError(
                    "Prepare complete immutable run identities before creating experiments"
                )
            # Matched bounded loops: under a FEEDBACK_V1 declaration the
            # random baseline runs the seeded RANDOM_V1 loop (same slot,
            # candidate and target caps, no planner), adaptive arms run
            # FEEDBACK_V1. Under ONE_SHOT every arm stays one-shot.
            if protocol.primary_policy == "FEEDBACK_V1":
                effective_policy = "RANDOM_V1" if arm == "random_det" else "FEEDBACK_V1"
            else:
                effective_policy = "ONE_SHOT"
            marker = dict(
                protocol_sha256=protocol.sha256,
                case_id=case["case_id"],
                split=case["split"],
                strategy=arm,
                declared_policy=protocol.primary_policy,
                search_policy=effective_policy,
            )
            ref = s.start_experiment(
                StartExperiment(
                    project_id=self.project_id,
                    contract_id=cid,
                    target_config_id=target["id"],
                    mode="RANDOM_BASELINE" if arm == "random_det" else "ADAPTIVE",
                    search_policy=effective_policy,
                    random_seed=protocol.random_seed,
                    limits=limits,
                ),
                key,
                _evaluation={"evaluation": self.declaration, "evaluation_identity": marker},
                _experiment_id=UUID(self.ids_by_key[key]),
            )
            uow.commit()
            return str(ref.experiment_id)

    def bind(self, ids: list[str], groups: list[list[str]], started_at: str | None = None) -> None:
        """Validate the already-inserted declaration; never update frozen snapshots."""
        if (
            ids != self.declaration["experiment_ids"]
            or groups != self.declaration["baseline_groups"]
            or started_at is not None
            and started_at != self.declaration["started_at"]
        ):
            raise ValueError("Immutable run membership changed")
        from services.api.app.evaluation import complete_membership

        with self.factory() as uow:
            s = Service(uow, self.settings)
            first = s.experiment(UUID(ids[0]))
            if not complete_membership(s, first):
                raise ValueError("Incomplete atomic evaluation membership")

    def exclude(self, ids: list[str]) -> None:
        with self.factory() as uow:
            s = Service(uow, self.settings)
            for eid in ids:
                e = s.experiment(UUID(eid))
                if e["runner_token"]:
                    raise ValueError("Cannot exclude a running arm before accounting completes")
                transition(
                    s, UUID(eid), "SEED_INVALID_FOR_SEARCH", "MATCHED_BASELINE_NOT_ESTABLISHED"
                )
                uow.connection.execute(
                    s.t.experiments.update()
                    .where(s.t.experiments.c.id == UUID(eid))
                    .values(dispatch_pending=False)
                )
            uow.commit()

    async def tick(self, eid: str, phase: str) -> str:
        for _ in range(self.protocol.max_api_calls * 10 + 100):
            with self.factory() as uow:
                s = Service(uow, self.settings)
                e = s.experiment(UUID(eid))
                if phase == "BASELINE" and e["state"] == "READY":
                    return "READY"
                if e["state"] in {
                    "PAUSED",
                    "COMPLETED",
                    "FAILED",
                    "CANCELLED",
                    "BUDGET_EXHAUSTED",
                    "SEED_INVALID_FOR_SEARCH",
                }:
                    return str(e["state"])
                before = (e["state"], e["delivery_no"])
            await execute_experiment(self.factory, self.settings, UUID(eid))
            with self.factory() as uow:
                e = Service(uow, self.settings).experiment(UUID(eid))
                if (e["state"], e["delivery_no"]) == before:
                    raise ValueError(
                        "Worker is fenced, awaiting peer baseline, or needs recovery; no redispatch"
                    )
        raise ValueError("Evaluation stage limit reached; inspect durable state")

    def source(self, ids: list[str]) -> dict[str, Any]:
        """Snapshot source rows with verified evidence; no model key or target secret_ref."""
        result: dict[str, Any] = dict(
            provenance="REAL_PROVIDER"
            if self.settings.provider_mode == "LIVE"
            else "LOCAL_FIXTURE",
            protocol=self.protocol.model_dump(mode="json"),
            experiments=[],
            usage=[],
            observations=[],
            mutations=[],
            plans=[],
            findings=[],
            candidates=[],
            audits=[],
        )
        with self.factory() as uow:
            s = Service(uow, self.settings)
            for eid in map(UUID, ids):
                e = s.experiment(eid)
                result["experiments"].append(
                    {**dict(e), **e["config_snapshot"]["evaluation_identity"]}
                )
                for label, table in (
                    ("usage", s.t.usage_events),
                    ("observations", s.t.model_observations),
                    ("mutations", s.t.mutations),
                    ("plans", s.t.mutation_plans),
                    ("findings", s.t.findings),
                ):
                    result[label].extend(
                        dict(r)
                        for r in s.db.execute(
                            select(table)
                            .where(table.c.experiment_id == eid)
                            .order_by(table.c.created_at, table.c.id)
                        ).mappings()
                    )
            findings = {f["mutation_id"]: f for f in result["findings"]}
            exps = {e["id"]: e for e in result["experiments"]}
            for m in result["mutations"]:
                if not m["image_asset_id"]:
                    continue
                row = s.candidates.latest(m["id"])
                checked = s.candidates.checked_report(s.candidates.row(m["id"]))
                # Automatic verdict: latest validation with no human reviewer.
                # A human-approved VALID is never relabeled automatic.
                validations = s.t.mutation_validations
                automatic = s.db.execute(
                    select(validations.c.verdict)
                    .where(
                        validations.c.mutation_id == m["id"],
                        validations.c.reviewer.is_(None),
                    )
                    .order_by(validations.c.revision.desc())
                    .limit(1)
                ).scalar()
                asset = s.asset(m["image_asset_id"])
                s.artifacts.read_verified(asset["id"])
                contract = exps[m["experiment_id"]]["config_snapshot"]["contract"]
                candidate = dict(
                    candidate_id=str(m["id"]),
                    experiment_id=str(m["experiment_id"]),
                    strategy=exps[m["experiment_id"]]["strategy"],
                    family=m["family"],
                    task_question=contract["prompt"],
                    expected_count=contract["expected"]["value"],
                    image_sha256=asset["sha256"],
                    evidence_sha256=fingerprint(row["evidence_json"]),
                    candidate_sha256=fingerprint(
                        {
                            k: m[k]
                            for k in ("id", "recipe_json", "image_asset_id", "family", "strength")
                        }
                    ),
                    original_verdict=checked.verdict,
                    current_verdict=checked.verdict,
                    automatic_verdict=automatic,
                    candidate_reviewer=row["reviewer"],
                    stable=findings.get(m["id"], {}).get("status") == "STABLE",
                    showcase=bool(findings.get(m["id"], {}).get("demo_approved_at")),
                )
                result["candidates"].append(candidate)
        import json

        return dict(json.loads(json.dumps(result, default=json_default)))
