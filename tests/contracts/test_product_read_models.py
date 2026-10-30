"""Owner UI metrics keep measured denominators distinct from absent legacy fields."""

from counterforge_contracts.responses import Metrics

BASE = {
    "generated_candidates": 2,
    "valid_candidates": 1,
    "invalid_candidates": 1,
    "review_required_candidates": 0,
    "valid_screening_calls": 1,
    "total_target_calls": 5,
    "stable_findings": 0,
    "actual_provider_usage_usd": 0,
    "conservative_estimate_usd": 0,
    "unresolved_charges_usd": 0,
}


def test_legacy_metrics_do_not_invent_measured_denominators():
    result = Metrics.model_validate(BASE).model_dump(mode="json")
    assert result["screening"] is None and result["confirmation"] is None
    assert result["finding_counts"] == {} and result["report_references"] == []
    assert result["spend_by_category"] == {}


def test_metrics_include_explicit_uncertain_denominators_and_cost_categories():
    result = Metrics.model_validate(
        {
            **BASE,
            "screening": {
                "total": 3,
                "interpretable": 1,
                "correct": 0,
                "incorrect": 1,
                "unknown": 1,
                "provider_errors": 1,
            },
            "finding_counts": {"INTERMITTENT": 1, "REVIEW_REQUIRED": 1},
            "spend_by_category": {
                "TARGET": {"actual_usd": 0.02, "unresolved_usd": 0.05, "reserved_usd": 0.03}
            },
        }
    )
    assert result.screening.interpretable == 1
    assert result.screening.total == 3
    assert result.spend_by_category["TARGET"].unresolved_usd == 0.05
