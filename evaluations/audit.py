"""Reproducible random precision sample and separate exhaustive finding audits."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from .dataset import FrozenModel, Hash, ReviewTime, Text
from .integrity import digest, seal, verify


class AuditDecision(FrozenModel):
    candidate_id: Text
    candidate_sha256: Hash
    image_sha256: Hash
    evidence_sha256: Hash
    original_verdict: Literal["VALID", "INVALID", "REVIEW_REQUIRED"]
    automatic_verdict: Literal["VALID", "INVALID", "REVIEW_REQUIRED"] | None = None
    reviewer: Text
    independent: Literal[True]
    reviewed_at: ReviewTime
    judgment: Literal["PASS", "FAIL", "AMBIGUOUS"]
    rationale: Text
    supersedes: Hash | None = None
    sha256: Hash


BINDING = (
    "candidate_id",
    "candidate_sha256",
    "image_sha256",
    "evidence_sha256",
    "original_verdict",
)


def select_sample(candidates: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    ids = [c["candidate_id"] for c in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate audit candidate")
    strata: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for c in candidates:
        if c["original_verdict"] == "REVIEW_REQUIRED":
            continue
        strata[
            c["strategy"],
            c["family"],
            "accepted" if c["original_verdict"] == "VALID" else "rejected",
        ].append(c)
    selected: dict[str, list[str]] = {"accepted": [], "rejected": []}
    sizes = []
    for (strategy, family, verdict), rows in sorted(strata.items()):
        ordered = sorted(
            rows,
            key=lambda c: (
                digest([seed, strategy, family, verdict, c["candidate_id"]]),
                c["candidate_id"],
            ),
        )[:5]
        selected[verdict].extend(c["candidate_id"] for c in ordered)
        sizes.append(
            dict(
                strategy=strategy,
                family=family,
                verdict=verdict,
                available=len(rows),
                selected=len(ordered),
            )
        )
    targeted = sorted(c["candidate_id"] for c in candidates if c.get("stable") or c.get("showcase"))
    all_ids = set(selected["accepted"] + selected["rejected"] + targeted)
    blinded = [
        {k: c[k] for k in ("candidate_id", "candidate_sha256", "image_sha256", "evidence_sha256")}
        for c in sorted(candidates, key=lambda c: digest([seed, c["candidate_id"]]))
        if c["candidate_id"] in all_ids
    ]
    return seal(
        dict(
            sampling_seed=seed,
            population=sorted(candidates, key=lambda c: c["candidate_id"]),
            population_sha256=digest(sorted(candidates, key=lambda c: c["candidate_id"])),
            pending_population=sorted(
                c["candidate_id"] for c in candidates if c["original_verdict"] == "REVIEW_REQUIRED"
            ),
            random_accepted=selected["accepted"],
            random_rejected=selected["rejected"],
            targeted=targeted,
            strata=sizes,
            blinded_export=blinded,
        )
    )


def decisions_for(
    candidate: dict[str, Any], decisions: list[dict[str, Any]]
) -> list[AuditDecision]:
    chain: list[AuditDecision] = []
    for data in decisions:
        if data.get("candidate_id") != candidate["candidate_id"]:
            continue
        # Verify the sealed record as written; newer optional fields get
        # their model default without invalidating older sealed decisions.
        verify(data)
        row = AuditDecision.model_validate(data)
        if any(getattr(row, k) != candidate[k] for k in BINDING):
            raise ValueError(
                "Audit is bound to different candidate/image/evidence/original verdict"
            )
        if candidate.get("automatic_verdict") is not None and (
            row.automatic_verdict != candidate["automatic_verdict"]
        ):
            raise ValueError("Audit is bound to a different automatic verdict")
        if row.supersedes != (chain[-1].sha256 if chain else None):
            raise ValueError("Corrections must append to an immutable audit chain")
        if chain and row.reviewed_at < chain[-1].reviewed_at:
            raise ValueError("Correction review precedes original review")
        if row.reviewer in {
            candidate.get("automatic_reviewer"),
            candidate.get("candidate_reviewer"),
        }:
            raise ValueError("Independent audit cannot reuse the candidate reviewer")
        chain.append(row)
    return chain


def eligible(candidate: dict[str, Any], decisions: list[dict[str, Any]]) -> bool:
    chain = decisions_for(candidate, decisions)
    return bool(chain and chain[-1].judgment == "PASS" and candidate["original_verdict"] == "VALID")


def audit_precision(
    candidates: list[dict[str, Any]], sample: dict[str, Any], decisions: list[dict[str, Any]]
) -> dict[str, Any]:
    verify(sample)
    if sample.get("population_sha256") != digest(
        sorted(candidates, key=lambda c: c["candidate_id"])
    ):
        raise ValueError("Frozen audit population changed")
    selected = set(sample["random_accepted"])
    rows = [c for c in candidates if c["candidate_id"] in selected]
    if len(rows) != len(selected):
        raise ValueError("Audit sample references missing candidate")
    chains = [decisions_for(c, decisions) for c in rows]
    reviewed = sum(bool(c) for c in chains)
    passed = sum(bool(c and c[-1].judgment == "PASS") for c in chains)
    selected_n = len(rows)
    # Spec 16.3: precision is confirmed / all *audited* accepted samples.
    # The selected size and pending count stay visible separately, and the
    # headline value stays null until every selected sample is reviewed so
    # a partial audit cannot pose as the completed precision.
    return dict(
        numerator=passed,
        denominator=reviewed,
        selected=selected_n,
        reviewed=reviewed,
        pending=selected_n - reviewed,
        value=passed / reviewed if reviewed and reviewed == selected_n else None,
    )
