"""Pure frozen initial policy. Estimates are engineering defaults, not measurements."""

from math import sqrt
from typing import Any

from counterforge_contracts.history import ProposalRanking
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.vtc import VisualTestContract
from counterforge_meta.history import clean_text
from counterforge_mutations.registry import approve_plan

POLICY_REVISION = "feedback-v1"
SLOT_STRATEGY = "ordinal-mod5-zero-random-v1"


def exploration_slot(slot: int) -> bool:
    return slot % 5 == 0


def rank_proposals(
    proposals: list[MutationPlan],
    contract: VisualTestContract,
    history: list[dict[str, Any]],
    *,
    available_usd: float,
    cost_components: dict[str, float],
) -> list[ProposalRanking]:
    rankings = []
    for index, plan in enumerate(proposals):
        outcomes = [h for h in history if h.get("family") == plan.family]
        valid = sum(h.get("validity") == "VALID" for h in outcomes)
        invalid = sum(h.get("validity") == "INVALID" for h in outcomes)
        screening = [
            o["outcome"]
            for h in outcomes
            if h.get("validity") == "VALID"
            for o in h.get("target_outcomes", [])
            if o.get("phase") == "SCREENING"
        ]
        correct, incorrect = screening.count("CORRECT"), screening.count("INCORRECT")
        pvalid, pfail = (
            (valid + 1) / (valid + invalid + 2),
            (incorrect + 1) / (correct + incorrect + 2),
        )
        cost = sum(cost_components.get(k, 0) for k in ("sam", "screen", "confirmation"))
        if plan.requires_muse_image:
            cost += cost_components.get("generation", 0)
        reason = None
        try:
            approve_plan(contract, plan)
        except ValueError:
            reason = "INVALID_PROPOSAL"
        if reason is None and cost > available_usd:
            reason = "UNAFFORDABLE"
        rankings.append(
            ProposalRanking(
                proposal_order=index,
                family=plan.family,
                strength=plan.strength,
                hypothesis=clean_text(plan.hypothesis),
                estimated_cost_usd=cost,
                p_valid=pvalid,
                p_failure=pfail,
                family_trials=len(outcomes),
                explore=1 / sqrt(1 + len(outcomes)),
                score=(pvalid * pfail + 0.1 / sqrt(1 + len(outcomes))) / max(0.000001, cost),
                rejection_reason=reason,
            )
        )
    eligible = sorted(
        (r for r in rankings if r.rejection_reason is None),
        key=lambda r: (-r.score, r.family, r.proposal_order),
    )
    for rank, row in enumerate(eligible, 1):
        row.rank, row.selected = rank, rank == 1
    return rankings
