"""Deterministic, source-reconciled descriptive metrics with explicit denominators."""

from __future__ import annotations

import csv
from collections import Counter
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import StringIO
from pathlib import Path
from typing import Any

from services.api.app.comparison import summarize_usage
from services.api.app.evaluation import GLOBAL_EVALUATION_STOP_REASON

from .audit import audit_precision, eligible, select_sample
from .integrity import atomic_write, canonical, seal, verify


def ratio(numerator: int, denominator: int) -> dict[str, Any]:
    if numerator < 0 or denominator < 0:
        raise ValueError("Negative metric count")
    return dict(
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator if denominator else None,
    )


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def usage_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    converted = [
        {
            **r,
            "actual_usd": Decimal(str(r.get("actual_usd") or 0)),
            "accounted_estimate_usd": Decimal(str(r.get("accounted_estimate_usd") or 0)),
            "estimated_max_usd": Decimal(str(r.get("estimated_max_usd") or 0)),
            "dispatched_at": r.get("dispatched_at"),
            "cost_kind": r.get("cost_kind"),
        }
        for r in rows
    ]
    return summarize_usage(converted)


def unique(rows: list[dict[str, Any]], key: str, label: str) -> None:
    values = [r[key] for r in rows]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {label} {key}")


def reconcile(source: dict[str, Any]) -> None:
    for label in ("experiments", "usage", "observations", "mutations", "plans", "findings"):
        unique(source[label], "id", label)
    unique(source["usage"], "operation_key", "usage")
    experiments = {e["id"]: e for e in source["experiments"]}
    usage = {u["operation_key"]: u for u in source["usage"]}
    mutations = {m["id"]: m for m in source["mutations"]}
    plans = {p["id"]: p for p in source["plans"]}
    for label in ("usage", "observations", "mutations", "plans", "findings"):
        for row in source[label]:
            if row.get("experiment_id", row.get("source_experiment_id")) not in experiments:
                raise ValueError(f"{label} has an unknown experiment")
    for o in source["observations"]:
        u = usage.get(o.get("operation_key"))
        if (
            not u
            or u["category"] != "TARGET"
            or not u.get("dispatched_at")
            or u["experiment_id"] != o["experiment_id"]
        ):
            raise ValueError("Observation/usage reconciliation failed")
        if o.get("mutation_id") and o["mutation_id"] not in mutations:
            raise ValueError("Observation missing mutation source")
    for m in source["mutations"]:
        if m.get("plan_id") and (
            m["plan_id"] not in plans or plans[m["plan_id"]]["experiment_id"] != m["experiment_id"]
        ):
            raise ValueError("Mutation/plan reconciliation failed")
    for f in source["findings"]:
        if (
            f["mutation_id"] not in mutations
            or mutations[f["mutation_id"]]["experiment_id"] != f["experiment_id"]
        ):
            raise ValueError("Finding/mutation reconciliation failed")
    for e in experiments.values():
        consumed = sum(
            u["request_count"]
            for u in usage.values()
            if u["experiment_id"] == e["id"]
            and u["category"] == "TARGET"
            and u.get("dispatched_at")
        )
        if consumed != e["calls_consumed"] or consumed > e["max_target_calls"]:
            raise ValueError("Experiment target counter/usage reconciliation failed")
    if source.get("protocol"):
        from .protocol import Protocol, validate_protocol

        protocol = Protocol.model_validate(source["protocol"])
        validate_protocol(protocol)
        exposure = usage_totals(source["usage"])
        if (
            sum(u["request_count"] for u in source["usage"] if u["status"] != "VOID")
            > protocol.max_api_calls
        ):
            raise ValueError("Global API authorization exceeded")
        if (
            sum(
                Decimal(exposure[k])
                for k in ("actual_usd", "estimated_usd", "unknown_usd", "reserved_usd")
            )
            > protocol.max_usd
        ):
            raise ValueError("Global dollar authorization exceeded")
        if any(
            e["max_target_calls"] != protocol.target_calls_per_arm for e in experiments.values()
        ):
            raise ValueError("Unequal target caps")


def summarize(source: dict[str, Any], sample: dict[str, Any] | None = None) -> dict[str, Any]:
    reconcile(source)
    candidates = source["candidates"]
    audits = source["audits"]
    seed = source.get("protocol", {}).get("random_seed", 20261030)
    sample = sample or select_sample(candidates, seed)
    verify(sample)
    # Freeze the review population/verdict rather than silently moving strata.
    if "population" in sample:
        pinned = {c["candidate_id"]: c for c in sample["population"]}
        if set(pinned) != {c["candidate_id"] for c in candidates}:
            raise ValueError(
                "Frozen audit population changed; start a separately labeled report revision"
            )
        for c in candidates:
            old = pinned[c["candidate_id"]]
            if any(
                c[k] != old[k]
                for k in ("candidate_sha256", "image_sha256", "evidence_sha256", "original_verdict")
            ):
                raise ValueError("Frozen candidate/evidence/original verdict changed")
            if (
                "automatic_verdict" in old
                and c.get("automatic_verdict") != old["automatic_verdict"]
            ):
                raise ValueError("Frozen automatic verdict changed")
    candidate_by_id = {c["candidate_id"]: c for c in candidates}
    if any(a["candidate_id"] not in candidate_by_id for a in audits):
        raise ValueError("Audit references missing source candidate")
    # Validate every audit chain, including rejected/targeted rows.
    from .audit import decisions_for

    for c in candidates:
        decisions_for(c, audits)
    baselines = {
        e["id"]: [
            o["outcome"]
            for o in source["observations"]
            if o["experiment_id"] == e["id"] and o["phase"] == "SEED_BASELINE"
        ]
        for e in source["experiments"]
    }
    excluded_cases = {
        e["case_id"]
        for e in source["experiments"]
        if baselines[e["id"]] != ["CORRECT"] * 3
        or e.get("stop_reason") == "MATCHED_BASELINE_NOT_ESTABLISHED"
    }
    arms = []
    for e in source["experiments"]:
        eid = e["id"]
        usage = [r for r in source["usage"] if r["experiment_id"] == eid]
        observations = [r for r in source["observations"] if r["experiment_id"] == eid]
        mutations = [m for m in source["mutations"] if m["experiment_id"] == eid]
        # Refinement and retest remain in all-cost exposure but not discovery numerators.
        refinement_ids = {m["id"] for m in mutations if m.get("minimization_job_id")}
        mutations = [m for m in mutations if m["id"] not in refinement_ids]
        discovery_obs = [
            o
            for o in observations
            if not o.get("retest_job_id")
            and o["phase"] != "RETEST"
            and o.get("mutation_id") not in refinement_ids
        ]
        findings = [
            f
            for f in source["findings"]
            if f["experiment_id"] == eid and f["mutation_id"] not in refinement_ids
        ]
        valid_ids = {
            c["candidate_id"]
            for c in candidates
            if c.get("experiment_id") == eid
            and c.get("current_verdict", c["original_verdict"]) == "VALID"
        }
        statuses = Counter(
            c.get("current_verdict", c["original_verdict"])
            for c in candidates
            if c.get("experiment_id") == eid and c["candidate_id"] not in refinement_ids
        )
        stable = []
        audit_holds = 0
        non_audit_drops = 0
        exclusion_drops = 0
        for f in findings:
            c = candidate_by_id.get(f["mutation_id"])
            confirmations = [
                o
                for o in discovery_obs
                if o.get("mutation_id") == f["mutation_id"] and o["phase"] == "CONFIRMATION"
            ]
            wrong_screen = any(
                o.get("mutation_id") == f["mutation_id"]
                and o["phase"] == "SCREENING"
                and o["outcome"] == "INCORRECT"
                for o in discovery_obs
            )
            if f["status"] == "STABLE" and (
                len(confirmations) != 3 or any(o["outcome"] != "INCORRECT" for o in confirmations)
            ):
                raise ValueError(
                    "Stable finding lacks three fresh incorrect confirmation source rows"
                )
            if f["status"] != "STABLE":
                continue
            if e["case_id"] in excluded_cases:
                exclusion_drops += 1
            # Non-audit gates only: baseline membership, valid current
            # candidate, genuine incorrect screen, canonical confirmations
            # (validated above). The independent audit is the only
            # remaining gate; failing it is an audit hold, not an exclusion.
            non_audit_ok = bool(
                c
                and e["case_id"] not in excluded_cases
                and wrong_screen
                and f["mutation_id"] in valid_ids
            )
            if not non_audit_ok:
                non_audit_drops += 1
            elif c is not None and eligible(c, audits):
                stable.append(f)
            else:
                audit_holds += 1
        stable_count = len(stable)
        generated = sum(bool(m.get("image_asset_id")) for m in mutations)
        screens = sum(
            o["phase"] == "SCREENING" and o.get("mutation_id") in valid_ids for o in discovery_obs
        )
        paid = sum(
            u["request_count"]
            for u in usage
            if u["category"] == "TARGET" and u.get("dispatched_at")
        )
        stable_times = [
            max(
                o["created_at"]
                for o in discovery_obs
                if o.get("mutation_id") == f["mutation_id"] and o["phase"] == "CONFIRMATION"
            )
            for f in stable
        ]
        first = min(stable_times) if stable_times else None
        consumed_to_first = sum(
            u["request_count"]
            for u in usage
            if u["category"] == "TARGET"
            and u.get("dispatched_at")
            and (first is None or u["dispatched_at"] <= first)
        )
        end = (
            first
            or e.get("ended_at")
            or max((u["dispatched_at"] for u in usage if u.get("dispatched_at")), default=None)
        )
        elapsed = (
            max(
                0.0,
                (
                    datetime.fromisoformat(end) - datetime.fromisoformat(e["started_at"])
                ).total_seconds(),
            )
            if end and e.get("started_at")
            else None
        )
        rate = ratio(stable_count, screens)
        arms.append(
            dict(
                experiment_id=eid,
                case_id=e["case_id"],
                split=e["split"],
                strategy=e["strategy"],
                search_policy=e.get("search_policy"),
                declared_policy=e.get("declared_policy"),
                comparison="generator_ablation"
                if e["strategy"] == "full_system"
                else "primary_deterministic",
                state=e["state"],
                stop_reason=e.get("stop_reason"),
                globally_censored=e.get("stop_reason") == GLOBAL_EVALUATION_STOP_REASON,
                excluded=e["case_id"] in excluded_cases,
                baseline_outcomes=baselines[eid],
                target_cap=e["max_target_calls"],
                proposals=len([p for p in source["plans"] if p["experiment_id"] == eid]),
                generated=generated,
                validity={v: statuses[v] for v in ("VALID", "INVALID", "REVIEW_REQUIRED")},
                valid_screening_calls=screens,
                all_paid_target_calls=paid,
                observations_by_phase=dict(Counter(o["phase"] for o in observations)),
                suspected=len(findings),
                stable_automatic=sum(f["status"] == "STABLE" for f in findings),
                stable_audited=stable_count,
                intermittent=sum(f["status"] == "INTERMITTENT" for f in findings),
                stable_per_valid_screen=rate,
                stable_per_100_valid_screens={
                    **rate,
                    "value": rate["value"] * 100 if rate["value"] is not None else None,
                },
                stable_per_paid_target=ratio(stable_count, paid),
                stable_over_suspected=ratio(stable_count, len(findings)),
                invalid_per_generated=ratio(statuses["INVALID"], generated),
                first_discovery=dict(
                    censored=first is None,
                    target_calls=consumed_to_first,
                    seconds=elapsed,
                    event_at=first,
                    definition="third fresh incorrect confirmation of independently passing finding",
                ),
                followups=dict(
                    refined_mutations=len(refinement_ids),
                    retest_observations=sum(o["phase"] == "RETEST" for o in observations),
                ),
                usage=usage_totals(usage),
                usage_by_category={
                    c: usage_totals([u for u in usage if u["category"] == c])
                    for c in ("TARGET", "PLANNER", "IMAGE", "SAM", "OTHER")
                },
                model_versions_observed=sorted(
                    {
                        str(o["model_version_observed"])
                        for o in observations
                        if o.get("model_version_observed")
                    }
                ),
                generation_failures=len(mutations) - generated,
                provider_error_observations=sum(
                    o["outcome"] in {"PROVIDER_ERROR", "UNKNOWN"} for o in discovery_obs
                ),
                image_sam_unsettled=sum(
                    u["category"] in {"IMAGE", "SAM"} and u["status"] != "SETTLED" for u in usage
                ),
                audit_holds=audit_holds,
                non_audit_drops=non_audit_drops,
                exclusion_drops=exclusion_drops,
            )
        )
    matched = [a for a in arms if not a["excluded"] and a["strategy"] != "full_system"]
    random = sum(a["stable_audited"] for a in matched if a["strategy"] == "random_det")
    spark = sum(a["stable_audited"] for a in matched if a["strategy"] == "spark_det")
    # Matched-group completeness gates any comparative reading. A partial
    # creation snapshot, excluded seed, or non-terminal arm invalidates lift.
    primary_arms = {"random_det", "spark_det"}
    matched_groups = []
    incomplete_reasons: list[str] = []
    for case_id in sorted({a["case_id"] for a in arms}):
        group = [a for a in arms if a["case_id"] == case_id]
        present_primary = {a["strategy"] for a in group} & primary_arms
        missing_primary = sorted(primary_arms - present_primary)
        missing_all = sorted(
            {"random_det", "spark_det", "full_system"} - {a["strategy"] for a in group}
        )
        excluded = case_id in excluded_cases
        globally_censored = sorted(
            a["experiment_id"]
            for a in group
            if a["strategy"] in primary_arms and a["globally_censored"]
        )
        non_terminal = sorted(
            a["experiment_id"]
            for a in group
            if a["strategy"] in primary_arms
            and (a["state"] not in TERMINAL_COMPARISON_STATES or a["globally_censored"])
        )
        primary_complete = not missing_primary and not excluded and not non_terminal
        if missing_primary:
            incomplete_reasons.append(f"{case_id}: missing primary arms {missing_primary}")
        if missing_all:
            incomplete_reasons.append(f"{case_id}: partial creation, missing arms {missing_all}")
        if excluded:
            incomplete_reasons.append(f"{case_id}: baseline-excluded from matched comparison")
        if globally_censored:
            incomplete_reasons.append(
                f"{case_id}: globally censored primary arms {globally_censored}"
            )
        if non_terminal:
            incomplete_reasons.append(f"{case_id}: non-terminal primary arms {non_terminal}")
        matched_groups.append(
            dict(
                case_id=case_id,
                strategies=sorted(a["strategy"] for a in group),
                missing_primary_arms=missing_primary,
                missing_arms=missing_all,
                excluded=excluded,
                globally_censored_primary=globally_censored,
                non_terminal_primary=non_terminal,
                primary_complete=primary_complete,
                group_complete=not missing_all and not excluded and not non_terminal,
            )
        )
    primary_comparison_valid = bool(matched_groups) and all(
        g["primary_complete"] for g in matched_groups
    )
    all_versions = sorted({v for a in arms for v in a["model_versions_observed"]})
    audit_chains = {c["candidate_id"]: decisions_for(c, audits) for c in candidates}
    audit_summary = dict(
        reviewed=sum(bool(v) for v in audit_chains.values()),
        passed=sum(bool(v and v[-1].judgment == "PASS") for v in audit_chains.values()),
        failed=sum(bool(v and v[-1].judgment == "FAIL") for v in audit_chains.values()),
        ambiguous=sum(bool(v and v[-1].judgment == "AMBIGUOUS") for v in audit_chains.values()),
        corrections=sum(len(v) - 1 for v in audit_chains.values() if len(v) > 1),
        corrected_candidates=sum(1 for v in audit_chains.values() if len(v) > 1),
        holds=sum(a["audit_holds"] for a in arms),
        non_audit_drops=sum(a["non_audit_drops"] for a in arms),
        exclusion_drops=sum(a["exclusion_drops"] for a in arms),
        pending=len(candidates) - sum(bool(v) for v in audit_chains.values()),
    )
    search_by_strategy: dict[str, Any] = {}
    for a in arms:
        search_by_strategy.setdefault(a["strategy"], a.get("search_policy"))
    loop_policies = {"FEEDBACK_V1", "RANDOM_V1"}
    matched_loop_semantics = (
        "matched bounded loops: adaptive FEEDBACK_V1 and seeded RANDOM_V1 "
        "share max_candidates/target-call caps and stop at caps or human "
        "review; RANDOM_V1 draws random_plan(seed+slot) each slot with no "
        "planner calls or outcome-conditioned ranking"
        if any(p in loop_policies for p in search_by_strategy.values())
        else "one-shot: each arm stops after its single bounded plan"
    )
    latencies = [
        u["latency_ms"]
        for u in source["usage"]
        if u.get("dispatched_at") and u.get("latency_ms") is not None
    ]
    return dict(
        format_version="1.0",
        provenance=source["provenance"],
        protocol_sha256=source.get("protocol", {}).get("sha256"),
        source_sha256=sha256(canonical(source)).hexdigest(),
        arms=arms,
        eligible_seeds=sorted({e["case_id"] for e in source["experiments"]} - excluded_cases),
        excluded_seeds=sorted(excluded_cases),
        failed_interrupted_excluded=[
            a["experiment_id"]
            for a in arms
            if a["excluded"]
            or a["globally_censored"]
            or a["state"] not in {"COMPLETED", "BUDGET_EXHAUSTED"}
        ],
        globally_censored=[a["experiment_id"] for a in arms if a["globally_censored"]],
        usage=usage_totals(source["usage"]),
        unsettled=any(u["status"] in {"RESERVED", "UNKNOWN_CHARGE"} for u in source["usage"]),
        latency_ms=dict(
            p50=percentile(latencies, 0.5), p95=percentile(latencies, 0.95), n=len(latencies)
        ),
        accepted_precision_random_only=audit_precision(candidates, sample, audits),
        targeted_audits=dict(
            selected=len(sample["targeted"]),
            independently_passing=sum(
                eligible(candidate_by_id[i], audits) for i in sample["targeted"]
            ),
        ),
        search_policies=search_by_strategy,
        matched_loop_semantics=matched_loop_semantics,
        matched_groups=matched_groups,
        primary_comparison_valid=primary_comparison_valid,
        comparison_incomplete_reasons=incomplete_reasons,
        observed_model_versions=all_versions,
        model_version_uncertainty=(
            "multiple observed versions; temporal drift cannot be excluded"
            if len(all_versions) > 1
            else "no observed version recorded"
            if not all_versions
            else "single observed version"
        ),
        generation_sam_summary=dict(
            generation_failures=sum(a["generation_failures"] for a in arms),
            provider_error_observations=sum(a["provider_error_observations"] for a in arms),
            image_sam_unsettled=sum(a["image_sam_unsettled"] for a in arms),
        ),
        audit_summary=audit_summary,
        primary_difference=dict(
            spark=spark,
            random=random,
            difference=spark - random if primary_comparison_valid else None,
            ratio=spark / random if (primary_comparison_valid and random) else None,
        ),
        real_model_headline_findings=sum(a["stable_audited"] for a in arms)
        if source["provenance"] == "REAL_PROVIDER"
        else 0,
        controls=dict(
            seam_only="NOT_RUN: independent validity review unavailable",
            sam_ablation="NOT_RUN: independent validity review unavailable",
        ),
        interpretation=(
            "Descriptive bounded evaluation. Heldout outcomes are reporting only. No superiority claim; fixture outcomes are not real-model findings."
            if primary_comparison_valid
            else "Incomplete matched comparison: comparative difference/ratio withheld. Descriptive bounded evaluation only; no superiority claim; fixture outcomes are not real-model findings."
        ),
    )


EXPECTED_BUNDLE_FILES = ("raw.json", "report.json", "report.csv", "audit-sample.json")
TERMINAL_COMPARISON_STATES = {"COMPLETED", "BUDGET_EXHAUSTED"}


def render_csv(report: dict[str, Any]) -> bytes:
    csv_text = StringIO(newline="")
    writer = csv.DictWriter(
        csv_text,
        fieldnames=[
            "experiment_id",
            "case_id",
            "split",
            "strategy",
            "state",
            "excluded",
            "target_cap",
            "proposals",
            "generated",
            "valid_screening_calls",
            "all_paid_target_calls",
            "suspected",
            "stable_automatic",
            "stable_audited",
            "intermittent",
        ],
    )
    writer.writeheader()
    for arm in report["arms"]:
        writer.writerow({k: arm[k] for k in writer.fieldnames or []})
    return csv_text.getvalue().encode()


def write_bundle(
    root: Path, source: dict[str, Any], report: dict[str, Any], sample: dict[str, Any] | None = None
) -> dict[str, str]:
    sample = sample or select_sample(
        source["candidates"], source.get("protocol", {}).get("random_seed", 20261030)
    )
    verify(sample)
    if report != summarize(source, sample):
        raise ValueError("Report-to-source reconciliation failed")
    files = {
        "raw.json": canonical(source),
        "report.json": canonical(report),
        "report.csv": render_csv(report),
        "audit-sample.json": canonical(sample),
    }
    hashes = {name: sha256(body).hexdigest() for name, body in files.items()}
    for name, body in files.items():
        path = root / name
        if path.exists() and path.read_bytes() != body:
            raise ValueError("Frozen bundle differs; use a new report revision directory")
    for name, body in files.items():
        atomic_write(root / name, body)
    atomic_write(root / "bundle-hashes.json", canonical(seal(dict(files=hashes))))
    return hashes


def verify_bundle(root: Path) -> None:
    import json

    hashes_path = root / "bundle-hashes.json"
    if not hashes_path.exists():
        raise ValueError("Bundle hashes file missing")
    hashes = json.loads(hashes_path.read_bytes())
    verify(hashes)
    listed = hashes.get("files")
    if not isinstance(listed, dict) or set(listed) != set(EXPECTED_BUNDLE_FILES):
        raise ValueError("Bundle must list exactly the four expected files")
    for name in EXPECTED_BUNDLE_FILES:
        path = root / name
        if not path.exists():
            raise ValueError(f"Bundle file missing: {name}")
        if sha256(path.read_bytes()).hexdigest() != listed[name]:
            raise ValueError("Bundle asset hash mismatch")
    source = json.loads((root / "raw.json").read_bytes())
    sample = json.loads((root / "audit-sample.json").read_bytes())
    report = json.loads((root / "report.json").read_bytes())
    if summarize(source, sample) != report:
        raise ValueError("Frozen report-to-source reconciliation failed")
    # Independently regenerate the CSV; a rehashed but inconsistent CSV fails.
    if (root / "report.csv").read_bytes() != render_csv(report):
        raise ValueError("Bundle CSV does not match regenerated report CSV")
