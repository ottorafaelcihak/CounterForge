"""Deterministic, local-only schema export. Does not load runtime secrets."""

import json
from pathlib import Path
from typing import Any

from counterforge_contracts.events import ExperimentEvent
from counterforge_contracts.mutations import MutationRecipe
from counterforge_contracts.plans import MutationPlan, PlannerProposal
from counterforge_contracts.providers import ProviderResult, SegmentationResult, TargetResult
from counterforge_contracts.vtc import VisualTestContract

from services.api.app.main import app

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "packages/contracts/schemas"


def render_schemas() -> dict[str, dict[str, Any]]:
    return {
        "vtc.schema.json": VisualTestContract.model_json_schema(),
        "mutation-plan.schema.json": MutationPlan.model_json_schema(),
        "mutation-recipe.schema.json": MutationRecipe.model_json_schema(),
        "planner-proposal.schema.json": PlannerProposal.model_json_schema(),
        "segmentation-result.schema.json": SegmentationResult.model_json_schema(),
        "target-result.schema.json": TargetResult.model_json_schema(),
        "provider-result.internal.schema.json": ProviderResult.model_json_schema(),
        "experiment-event.schema.json": ExperimentEvent.model_json_schema(),
        "openapi.json": app.openapi(),
    }


def main() -> None:
    SCHEMAS.mkdir(parents=True, exist_ok=True)
    for name, schema in sorted(render_schemas().items()):
        (SCHEMAS / name).write_text(
            json.dumps(schema, sort_keys=True, indent=2, allow_nan=False) + "\n"
        )
    print("Exported 9 deterministic application schemas (no provider calls).")


if __name__ == "__main__":
    main()
