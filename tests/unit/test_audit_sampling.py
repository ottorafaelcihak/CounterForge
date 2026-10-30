from datetime import UTC, datetime

import pytest

from evaluations.audit import AuditDecision, audit_precision, eligible, select_sample
from evaluations.integrity import seal


def candidates():
    return [
        dict(
            candidate_id=f"c{i}",
            strategy="random" if i < 20 else "spark",
            family="brightness_shift",
            image_sha256=f"{i:064x}",
            evidence_sha256="e" * 64,
            candidate_sha256="a" * 64,
            original_verdict="VALID" if i % 2 else "INVALID",
            stable=i in (1, 23),
            showcase=i == 3,
        )
        for i in range(40)
    ]


def decision(c, judgment="PASS", supersedes=None):
    return seal(
        dict(
            candidate_id=c["candidate_id"],
            candidate_sha256=c["candidate_sha256"],
            image_sha256=c["image_sha256"],
            evidence_sha256=c["evidence_sha256"],
            original_verdict=c["original_verdict"],
            automatic_verdict=c.get("automatic_verdict"),
            reviewer="independent-human",
            independent=True,
            reviewed_at=datetime.now(UTC).isoformat(),
            judgment=judgment,
            rationale="Count and visibility independently checked.",
            supersedes=supersedes,
        )
    )


def test_random_strata_separate_from_all_targeted_and_order_independent():
    rows = candidates()
    a = select_sample(rows, 17)
    assert a == select_sample(list(reversed(rows)), 17)
    assert len(a["random_accepted"]) == len(a["random_rejected"]) == 10
    assert set(a["targeted"]) == {"c1", "c3", "c23"}
    assert all("strategy" not in c and "stable" not in c for c in a["blinded_export"])


def test_pending_fail_corrections_and_precise_random_denominator():
    rows = candidates()
    sample = select_sample(rows, 17)
    c = next(c for c in rows if c["candidate_id"] == "c1")
    assert not eligible(c, [])
    passed = decision(c)
    assert eligible(c, [passed])
    failed = decision(c, "FAIL", passed["sha256"])
    assert not eligible(c, [passed, failed])
    partial = audit_precision(rows, sample, [passed])
    # Spec 16.3 denominator counts audited samples only; selection stays visible.
    assert partial["denominator"] == 1
    assert partial["selected"] == 10
    assert partial["reviewed"] == 1
    assert partial["pending"] == 9
    assert partial["numerator"] == 1
    assert partial["value"] is None
    with pytest.raises(ValueError):
        AuditDecision.model_validate({**passed, "reviewed_at": "2026-10-29T00:00:00"})
    with pytest.raises(ValueError):
        eligible(c, [passed, decision(c, "FAIL")])


def test_partial_audit_formula_uses_audited_denominator():
    rows = candidates()
    sample = select_sample(rows, 17)
    accepted = [c for c in rows if c["candidate_id"] in sample["random_accepted"]]
    decisions = [decision(accepted[0], "PASS"), decision(accepted[1], "FAIL")]
    result = audit_precision(rows, sample, decisions)
    assert result["numerator"] == 1
    assert result["denominator"] == 2
    assert result["selected"] == 10
    assert result["pending"] == 8
    assert result["value"] is None
    complete = [decision(c, "PASS") for c in accepted]
    done = audit_precision(rows, sample, complete)
    assert done["denominator"] == done["selected"] == 10
    assert done["value"] == 1.0


def test_automatic_verdict_bound_separately_from_human_valid():
    rows = candidates()
    candidate = next(c for c in rows if c["candidate_id"] == "c1")
    # Human-approved release verdict stays VALID; automatic was stricter.
    candidate["automatic_verdict"] = "REVIEW_REQUIRED"
    bound = decision(candidate)
    assert eligible(candidate, [bound])
    wrong = decision({**candidate, "automatic_verdict": "VALID"})
    with pytest.raises(ValueError, match="automatic verdict"):
        eligible(candidate, [wrong])
    # A decision omitting the available automatic verdict does not bind it.
    omitted = decision({k: v for k, v in candidate.items() if k != "automatic_verdict"})
    with pytest.raises(ValueError, match="automatic verdict"):
        eligible(candidate, [omitted])


def test_sample_binds_whole_population_and_pending_is_not_rejected():
    from copy import deepcopy

    rows = candidates()
    rows[0]["original_verdict"] = "REVIEW_REQUIRED"
    sample = select_sample(rows, 17)
    assert sample["pending_population"] == ["c0"]
    assert "c0" not in sample["random_rejected"]
    changed = deepcopy(rows)
    changed[9]["image_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="population"):
        audit_precision(changed, sample, [])
