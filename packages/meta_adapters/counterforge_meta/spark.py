"""Bounded structured Spark proposals with an explicitly attributed fallback."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from counterforge_contracts.plans import MutationPlan, PlannerProposal
from counterforge_contracts.versions import MutationFamily
from counterforge_contracts.vtc import Limits, VisualTestContract
from pydantic import JsonValue, ValidationError

from .transport import MetaTransport
from .usage import sanitize_raw

PLANNER_TEMPLATE = "planner-v1"


class SparkPlanner:
    def __init__(self, *, transport: MetaTransport) -> None:
        self.transport = transport
        self.last_evidence: dict[str, JsonValue] = {}

    async def plan(
        self,
        contract: VisualTestContract,
        history: list[dict[str, JsonValue]],
        remaining_budget: Limits,
    ) -> list[MutationPlan]:
        schema = PlannerProposal.model_json_schema()
        # ContractModel is already strict-root/all-required/additionalProperties=False.
        response_format: dict[str, Any] = {
            "type": "json_schema",
            "json_schema": {"name": "planner_proposal", "strict": True, "schema": schema},
        }
        payload = {
            "task": contract.task_type,
            "expected": contract.expected.value,
            "protected_label": contract.protected.label,
            "allowed_mutations": [family.value for family in contract.allowed_mutations],
            "forbidden_mutations": contract.forbidden_mutations,
            "min_visible_fraction": contract.protected.min_visible_fraction,
            "recent_outcomes": history[-10:],
            "remaining_budget": remaining_budget.model_dump(mode="json"),
        }
        text = json.dumps(payload, separators=(",", ":"), allow_nan=False)
        if len(text.encode()) > 32768:
            raise ValueError("planner context too large")
        response = await self.transport.chat(
            category="PLANNER",
            template_version=PLANNER_TEMPLATE,
            messages=[
                {
                    "role": "system",
                    "content": "Propose one authorized background robustness experiment. Use only allowed_mutations; preserve every protected object and the human ground truth. Never add or remove target instances.",
                },
                {"role": "user", "content": text},
            ],
            response_format=response_format,
            max_completion_tokens=1024,
        )
        raw = ""
        proposal = None
        reason = response.outcome
        if response.outcome == "COMPLETED":
            try:
                content = response.data["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("invalid proposal type")
                raw = content
                if len(raw.encode()) > 16384:
                    raise ValueError("invalid proposal size")
                proposal = PlannerProposal.model_validate_json(raw)
                plan = self._expand(proposal).validate_for_contract(contract)
            except (KeyError, IndexError, TypeError, ValueError, ValidationError):
                proposal = None
                reason = "INVALID"
        self.last_evidence = sanitize_raw(
            {
                "template_version": PLANNER_TEMPLATE,
                "proposal_sha256": sha256(raw.encode()).hexdigest(),
                "raw_proposal": raw,
                "author": "META_SPARK" if proposal else "DETERMINISTIC_FALLBACK",
                "fallback_reason": None if proposal else reason,
            }
        )
        if proposal is None:
            family = (
                MutationFamily.BRIGHTNESS_SHIFT
                if MutationFamily.BRIGHTNESS_SHIFT in contract.allowed_mutations
                else contract.allowed_mutations[0]
            )
            plan = self._expand(
                PlannerProposal(
                    family=family,
                    strength=0.15,
                    hypothesis=f"Deterministic fallback ({reason}): modest {family.value}",
                    prompt="Change the background modestly. Preserve all protected object pixels and introduce no target objects.",
                )
            )
            plan.validate_for_contract(contract)
        return [plan]

    def _expand(self, proposal: PlannerProposal) -> MutationPlan:
        return MutationPlan(
            **proposal.model_dump(),
            target_region="background_only",
            requires_muse_image=proposal.family != MutationFamily.BRIGHTNESS_SHIFT,
            estimated_cost_usd=(
                float(self.transport.prices.upper("muse-image-1.0"))
                if proposal.family != MutationFamily.BRIGHTNESS_SHIFT
                else 0.0
            ),
        )
