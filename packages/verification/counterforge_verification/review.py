"""Pure review resolution; authentication and append-only storage are backend duties."""

from dataclasses import asdict, dataclass
from typing import Literal

from counterforge_contracts.mutations import canonical_json, digest
from counterforge_contracts.responses import ReviewAudit, ValidationReport
from counterforge_contracts.versions import ValidityVerdict

from .invariants import (
    HUMAN_CODES,
    POLICY_VERSION,
    STRUCTURAL_CODES,
    VERIFIER_VERSION,
    aggregate_verdict,
    report_digest,
    seal_report,
)


@dataclass(frozen=True)
class GroundTruthAudit:
    """Explicit audit evidence for correcting failed semantic/visibility gates.

    Structural failures cannot be resolved here: repair evidence and reverify.
    The backend authenticates the auditor; this DTO makes no signature claim.
    """

    actor: str
    note: str
    prior_report_sha256: str
    evidence_hashes: dict[str, str]
    resolved_check_codes: tuple[str, ...]


@dataclass(frozen=True)
class HumanReview:
    actor: str
    note: str
    prior_report_sha256: str
    evidence_hashes: dict[str, str]
    semantic: Literal["consistent", "additional_object", "removed_object", "ambiguous"]
    visibility: Literal["clear", "ambiguous", "destroyed"]
    ground_truth_audit: GroundTruthAudit | None = None


def resolve_review(report: ValidationReport, decision: HumanReview) -> ValidationReport:
    """Bind a human decision to exact evidence; never silently erase a FAIL."""
    report = ValidationReport.model_validate_json(report.model_dump_json())
    codes = [c.code for c in report.checks]
    if (
        report.verifier_version != VERIFIER_VERSION
        or report.policy_version != POLICY_VERSION
        or len(codes) != len(set(codes))
        or not STRUCTURAL_CODES | HUMAN_CODES <= set(codes)
        or any(not c.mandatory for c in report.checks if c.code in STRUCTURAL_CODES | HUMAN_CODES)
        or not report.evidence_hashes
        or report.report_sha256 != report_digest(report)
        or report.verdict != aggregate_verdict(report.checks)
    ):
        raise ValueError("incomplete or altered prior verifier report")
    if (
        not decision.actor.strip()
        or not decision.note.strip()
        or decision.prior_report_sha256 != report.report_sha256
        or decision.evidence_hashes != report.evidence_hashes
        or decision.semantic
        not in {"consistent", "additional_object", "removed_object", "ambiguous"}
        or decision.visibility not in {"clear", "ambiguous", "destroyed"}
    ):
        raise ValueError("review must bind actor, note, prior report and exact evidence hashes")

    promoting = decision.semantic not in {"additional_object", "removed_object"}
    if (
        report.verdict == ValidityVerdict.INVALID
        and promoting
        and decision.ground_truth_audit is None
    ):
        raise ValueError("overriding INVALID requires a verified ground-truth audit")
    audit_hash = None
    audited: set[str] = set()
    if decision.ground_truth_audit is not None:
        audit = decision.ground_truth_audit
        audited = set(audit.resolved_check_codes)
        if (
            not audit.actor.strip()
            or not audit.note.strip()
            or audit.prior_report_sha256 != report.report_sha256
            or audit.evidence_hashes != report.evidence_hashes
            or len(audited) != len(audit.resolved_check_codes)
            or not audited <= HUMAN_CODES
            or any(c.code in audited and c.status != "FAIL" for c in report.checks)
        ):
            raise ValueError(
                "audit must explicitly resolve failed human gates against exact prior evidence"
            )
        audit_hash = digest(canonical_json(asdict(audit)).encode())

    semantic_status: Literal["PASS", "FAIL", "UNKNOWN"] = (
        "PASS"
        if decision.semantic == "consistent"
        else "UNKNOWN"
        if decision.semantic == "ambiguous"
        else "FAIL"
    )
    visibility_status: Literal["PASS", "FAIL", "UNKNOWN"] = (
        "PASS"
        if decision.visibility == "clear"
        else "FAIL"
        if decision.visibility == "destroyed"
        else "UNKNOWN"
    )
    for check in report.checks:
        if check.code not in HUMAN_CODES:
            continue
        if check.status == "FAIL" and check.code not in audited:
            continue
        check.status = semantic_status if check.code == "HUMAN_SEMANTICS" else visibility_status
        check.detail = f"Human review: {decision.semantic if check.code == 'HUMAN_SEMANTICS' else decision.visibility}."
    # Preserve structural checks verbatim; a failed raster/hash/recipe invariant
    # needs a new candidate/report, irrespective of an audit's favorable opinion.
    report.review_audit = ReviewAudit(
        actor=decision.actor.strip(),
        note=decision.note.strip(),
        previous_verdict=report.verdict,
        previous_report_sha256=decision.prior_report_sha256,
        reviewed_evidence_hashes=dict(decision.evidence_hashes),
        semantic=decision.semantic,
        visibility=decision.visibility,
        ground_truth_audit_sha256=audit_hash,
    )
    report.confidence_note = (
        "Human decision bound to exact evidence; structural failures remain mandatory."
    )
    return seal_report(report)
