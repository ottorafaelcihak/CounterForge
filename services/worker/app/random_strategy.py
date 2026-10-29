"""Reproducible P0 random control over the planner's permitted transform ranges."""

import random

from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.versions import MutationFamily

RANDOM_REVISION = "random-initial-v2"
STRENGTH_RANGE = (0.0, 1.0)


def random_plan(seed: int, families: list[MutationFamily], image_upper_usd: float) -> MutationPlan:
    if not families or len(set(families)) != len(families):
        raise ValueError("Random strategy needs distinct permitted families")
    rng = random.Random(seed)
    family = rng.choice(families)
    return MutationPlan(
        family=family,
        strength=round(rng.uniform(*STRENGTH_RANGE), 4),
        hypothesis="Predeclared seeded random baseline",
        prompt="Change only the background. Preserve protected objects.",
        requires_muse_image=family != "brightness_shift",
        estimated_cost_usd=image_upper_usd if family != "brightness_shift" else 0,
    )
