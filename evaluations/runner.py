"""Durable serial scheduling, with a separate all-arm baseline pass before search."""

from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from typing import Protocol as Port
from uuid import uuid4

from .integrity import atomic_write, canonical, digest, seal, verify
from .protocol import Protocol, validate_protocol


class ExperimentPort(Port):
    def create(self, key: str, case: dict[str, Any], arm: str, protocol: Protocol) -> str: ...
    async def tick(self, eid: str, phase: str) -> str: ...


class EvaluationRun:
    def __init__(
        self, root: Path, protocol: Protocol, cases: list[dict[str, Any]], adapter: ExperimentPort
    ) -> None:
        validate_protocol(protocol)
        self.root, self.protocol, self.adapter = root, protocol, adapter
        root.mkdir(parents=True, exist_ok=True)
        self.state_path = root / "state.json"
        self.case_hash = digest(cases)
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_bytes())
            verify(self.state)
            if (
                self.state["protocol_sha256"] != protocol.sha256
                or self.state["cases_sha256"] != self.case_hash
            ):
                raise ValueError("Resume requires immutable protocol and case ordering")
        else:
            self.state = dict(
                run_id=str(uuid4()),
                protocol_sha256=protocol.sha256,
                cases_sha256=self.case_hash,
                started_at=datetime.now(UTC).isoformat(),
                status="DECLARED",
                seeds=[
                    dict(
                        case=case,
                        status="PENDING",
                        arms=[
                            dict(
                                strategy=arm,
                                experiment_id=None,
                                baseline="PENDING",
                                search="PENDING",
                            )
                            for arm in protocol.order(i)
                        ],
                    )
                    for i, case in enumerate(cases)
                ],
            )
            self.save()
            atomic_write(root / "protocol.json", canonical(protocol.model_dump(mode="json")))

    def save(self) -> None:
        self.state = seal(self.state)
        atomic_write(self.state_path, canonical(self.state))

    async def run(self) -> None:
        # Refuse concurrent local coordinators; database fences cover workers.
        with (self.root / "coordinator.lock").open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            latest = json.loads(self.state_path.read_bytes())
            if latest != self.state:
                raise ValueError("Reload changed durable state before resuming")
            if self.state["status"] == "COMPLETED":
                return
            try:
                self.state["status"] = "RUNNING"
                self.save()
                intents = []
                for seed in self.state["seeds"]:
                    for arm in seed["arms"]:
                        arm["intent_key"] = (
                            self.state["run_id"]
                            + ":"
                            + seed["case"]["case_id"]
                            + ":"
                            + arm["strategy"]
                        )
                        intents.append(
                            dict(key=arm["intent_key"], case=seed["case"], arm=arm["strategy"])
                        )
                self.save()
                if hasattr(self.adapter, "prepare"):
                    self.adapter.prepare(intents, self.state["started_at"])
                for seed in self.state["seeds"]:
                    for arm in seed["arms"]:
                        if not arm["experiment_id"]:
                            arm["experiment_id"] = self.adapter.create(
                                arm["intent_key"], seed["case"], arm["strategy"], self.protocol
                            )
                            self.save()
                groups = [[a["experiment_id"] for a in s["arms"]] for s in self.state["seeds"]]
                if hasattr(self.adapter, "bind"):
                    self.adapter.bind(
                        [eid for g in groups for eid in g], groups, self.state["started_at"]
                    )
                for seed in self.state["seeds"]:
                    if seed["status"] in {"COMPLETED", "EXCLUDED"}:
                        continue
                    for arm in seed["arms"]:
                        if arm["baseline"] == "PENDING":
                            arm["baseline"] = await self.adapter.tick(
                                arm["experiment_id"], "BASELINE"
                            )
                            self.save()
                    if any(a["baseline"] != "READY" for a in seed["arms"]):
                        seed["status"] = "EXCLUDED"
                        if hasattr(self.adapter, "exclude"):
                            self.adapter.exclude([a["experiment_id"] for a in seed["arms"]])
                        self.save()
                        continue
                    for arm in seed["arms"]:
                        if arm["search"] not in {
                            "COMPLETED",
                            "FAILED",
                            "CANCELLED",
                            "BUDGET_EXHAUSTED",
                            "SEED_INVALID_FOR_SEARCH",
                        }:
                            arm["search"] = await self.adapter.tick(arm["experiment_id"], "SEARCH")
                            self.save()
                    seed["status"] = (
                        "WAITING_REVIEW"
                        if any(a["search"] == "PAUSED" for a in seed["arms"])
                        else "COMPLETED"
                    )
                    self.save()
                self.state["status"] = (
                    "WAITING_REVIEW"
                    if any(s["status"] == "WAITING_REVIEW" for s in self.state["seeds"])
                    else "COMPLETED"
                )
                self.save()
            except BaseException:
                self.state["status"] = "INTERRUPTED"
                self.save()
                raise
