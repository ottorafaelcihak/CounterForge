"""Phase 08 completion: fairness, cap-near confirmation, outage, packet, bundle."""

from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from evaluations.audit import select_sample
from evaluations.integrity import seal
from evaluations.packet import build_packet, verify_packet
from evaluations.report import summarize, verify_bundle, write_bundle
from services.worker.app.experiments import classify_confirmation
from tests.unit.test_audit_sampling import decision


def _img(color: tuple[int, int, int]) -> bytes:
    out = BytesIO()
    Image.new("RGB", (16, 16), color).save(out, format="PNG")
    return out.getvalue()


def _arm_source(*, invalid: int, valid_extra: int = 0, state: str = "COMPLETED") -> dict:
    """Two deterministic arms, equal cap 8, differing invalid rates, one stable each."""
    from evaluations.fixture import fixture_source

    rows = fixture_source()
    for index, arm in enumerate(("random_det", "spark_det")):
        eid = f"methodology-{index}"
        rows["experiments"][index]["calls_consumed"] = 7
        rows["experiments"][index]["state"] = state
        mid = f"mutation-{index}"
        rows["plans"].append(dict(id=f"plan-{mid}", experiment_id=eid))
        rows["mutations"].append(
            dict(id=mid, experiment_id=eid, plan_id=f"plan-{mid}", image_asset_id=f"image-{mid}")
        )
        rows["candidates"].append(
            dict(
                candidate_id=mid,
                experiment_id=eid,
                strategy=arm,
                family="brightness_shift",
                image_sha256="a" * 64,
                evidence_sha256="b" * 64,
                candidate_sha256="c" * 64,
                original_verdict="VALID",
                stable=True,
                showcase=False,
            )
        )
        rows["findings"].append(
            dict(id=f"finding-{mid}", experiment_id=eid, mutation_id=mid, status="STABLE")
        )
        for attempt in range(4):
            phase = "SCREENING" if attempt == 0 else "CONFIRMATION"
            key = f"{mid}:{phase}:{attempt}"
            stamp = f"2026-10-30T00:00:0{attempt + 4}+00:00"
            rows["usage"].append(
                dict(
                    id=key,
                    experiment_id=eid,
                    operation_key=key,
                    category="TARGET",
                    status="SETTLED",
                    cost_kind="ACTUAL",
                    actual_usd="0.01",
                    estimated_max_usd="0.02",
                    request_count=1,
                    dispatched_at=stamp,
                    created_at=stamp,
                    latency_ms=100,
                )
            )
            rows["observations"].append(
                dict(
                    id=key,
                    experiment_id=eid,
                    operation_key=key,
                    mutation_id=mid,
                    phase=phase,
                    attempt_no=attempt or 1,
                    outcome="INCORRECT",
                    created_at=stamp,
                    model_version_observed="fixture-v1",
                )
            )
        # Differing invalid rates: extra INVALID candidates with generated images.
        n_invalid = invalid if index == 0 else 0
        for j in range(n_invalid):
            bad = f"bad-{index}-{j}"
            rows["mutations"].append(
                dict(
                    id=bad, experiment_id=eid, plan_id=f"plan-{mid}", image_asset_id=f"image-{bad}"
                )
            )
            rows["candidates"].append(
                dict(
                    candidate_id=bad,
                    experiment_id=eid,
                    strategy=arm,
                    family="brightness_shift",
                    image_sha256="d" * 64,
                    evidence_sha256="e" * 64,
                    candidate_sha256="f" * 64,
                    original_verdict="INVALID",
                    stable=False,
                    showcase=False,
                )
            )
        for j in range(valid_extra if index == 1 else 0):
            extra = f"extra-{index}-{j}"
            rows["mutations"].append(
                dict(
                    id=extra,
                    experiment_id=eid,
                    plan_id=f"plan-{mid}",
                    image_asset_id=f"image-{extra}",
                )
            )
            rows["candidates"].append(
                dict(
                    candidate_id=extra,
                    experiment_id=eid,
                    strategy=arm,
                    family="brightness_shift",
                    image_sha256="1" * 64,
                    evidence_sha256="2" * 64,
                    candidate_sha256="3" * 64,
                    original_verdict="VALID",
                    stable=False,
                    showcase=False,
                )
            )
    return rows


def test_differing_invalid_rates_under_equal_total_target_caps():
    rows = _arm_source(invalid=3, valid_extra=1)
    rows["audits"] = [decision(c) for c in rows["candidates"] if c["original_verdict"] == "VALID"]
    result = summarize(rows)
    arms = {a["strategy"]: a for a in result["arms"] if a["strategy"] != "full_system"}
    assert arms["random_det"]["target_cap"] == arms["spark_det"]["target_cap"] == 8
    assert arms["random_det"]["all_paid_target_calls"] == arms["spark_det"]["all_paid_target_calls"]
    # random: 3 invalid / 4 generated; spark: 0 invalid / 2 generated.
    assert arms["random_det"]["invalid_per_generated"] == dict(
        numerator=3, denominator=4, value=0.75
    )
    assert arms["spark_det"]["invalid_per_generated"] == dict(numerator=0, denominator=2, value=0.0)
    assert result["primary_comparison_valid"] is True
    assert result["primary_difference"]["ratio"] == 1.0
    assert result["observed_model_versions"] == ["fixture-v1"]


def test_report_names_explicit_matched_loop_policies():
    rows = _arm_source(invalid=0)
    rows["experiments"][0].update(search_policy="RANDOM_V1", declared_policy="FEEDBACK_V1")
    rows["experiments"][1].update(search_policy="FEEDBACK_V1", declared_policy="FEEDBACK_V1")
    result = summarize(rows)
    assert result["search_policies"]["random_det"] == "RANDOM_V1"
    assert result["search_policies"]["spark_det"] == "FEEDBACK_V1"
    assert "matched bounded loops" in result["matched_loop_semantics"]
    assert "RANDOM_V1" in result["matched_loop_semantics"]


def test_confirmation_requires_three_fresh_or_no_promotion():
    assert classify_confirmation(["INCORRECT", "INCORRECT"]) == "REVIEW_REQUIRED"
    assert classify_confirmation(["INCORRECT"] * 3) == "STABLE"
    assert classify_confirmation(["INCORRECT", "CORRECT", "INCORRECT"]) == "INTERMITTENT"
    rows = _arm_source(invalid=0)
    # Drop one confirmation from the first arm but keep STABLE claim: must fail.
    rows["observations"] = [
        o
        for o in rows["observations"]
        if not (
            o["experiment_id"] == "methodology-0"
            and o["phase"] == "CONFIRMATION"
            and o["attempt_no"] == 3
        )
    ]
    rows["usage"] = [
        u
        for u in rows["usage"]
        if u["id"] in {o["id"] for o in rows["observations"]}
        or u["experiment_id"] == "methodology-2"
        or "baseline" in u["id"]
    ]
    # Reconcile calls_consumed with remaining dispatched TARGET usage.
    for exp in rows["experiments"]:
        exp["calls_consumed"] = sum(
            u["request_count"]
            for u in rows["usage"]
            if u["experiment_id"] == exp["id"]
            and u["category"] == "TARGET"
            and u.get("dispatched_at")
        )
    with pytest.raises(ValueError, match="three fresh"):
        summarize(rows)


def test_partial_confirmation_stays_suspected_and_censored():
    rows = _arm_source(invalid=0)
    rows["findings"][0]["status"] = "SUSPECTED"
    result = summarize(rows)
    arm = next(a for a in result["arms"] if a["strategy"] == "random_det")
    assert arm["stable_automatic"] == 0 and arm["stable_audited"] == 0
    assert arm["suspected"] == 1 and arm["first_discovery"]["censored"] is True


def test_provider_outage_arm_retained_not_deleted():
    rows = _arm_source(invalid=0, state="FAILED")
    for exp in rows["experiments"]:
        exp["state"] = "FAILED"
        exp["stop_reason"] = "PROVIDER_ERROR"
    result = summarize(rows)
    assert len(result["arms"]) == 3
    assert all(a["state"] == "FAILED" for a in result["arms"])
    assert result["primary_comparison_valid"] is False
    assert result["primary_difference"]["difference"] is None
    assert result["primary_difference"]["ratio"] is None
    assert result["failed_interrupted_excluded"]


def test_incomplete_partial_creation_snapshot_withholds_lift():
    rows = _arm_source(invalid=0)
    rows["experiments"] = rows["experiments"][:1]
    rows["usage"] = [u for u in rows["usage"] if u["experiment_id"] == "methodology-0"]
    rows["observations"] = [
        o for o in rows["observations"] if o["experiment_id"] == "methodology-0"
    ]
    rows["mutations"] = [m for m in rows["mutations"] if m["experiment_id"] == "methodology-0"]
    rows["plans"] = [p for p in rows["plans"] if p["experiment_id"] == "methodology-0"]
    rows["findings"] = [f for f in rows["findings"] if f["experiment_id"] == "methodology-0"]
    rows["candidates"] = [c for c in rows["candidates"] if c["experiment_id"] == "methodology-0"]
    result = summarize(rows)
    assert result["primary_comparison_valid"] is False
    assert any(
        "partial creation" in r or "missing primary" in r
        for r in result["comparison_incomplete_reasons"]
    )
    assert result["primary_difference"]["ratio"] is None


def test_rehashed_inconsistent_csv_and_missing_file_fail(tmp_path):
    rows = _arm_source(invalid=0)
    rows["audits"] = [decision(c) for c in rows["candidates"] if c["original_verdict"] == "VALID"]
    report = summarize(rows)
    write_bundle(tmp_path, rows, report)
    verify_bundle(tmp_path)
    # Tamper CSV then rehash + reseal: regeneration must still catch it.
    (tmp_path / "report.csv").write_text("tampered,csv\n1,2\n")
    hashes = json.loads((tmp_path / "bundle-hashes.json").read_bytes())
    hashes["files"]["report.csv"] = sha256((tmp_path / "report.csv").read_bytes()).hexdigest()
    (tmp_path / "bundle-hashes.json").write_text(json.dumps(seal(hashes)))
    with pytest.raises(ValueError, match="CSV|hash"):
        verify_bundle(tmp_path)


def test_missing_bundle_file_fails(tmp_path):
    rows = _arm_source(invalid=0)
    write_bundle(tmp_path, rows, summarize(rows))
    (tmp_path / "report.csv").unlink()
    with pytest.raises(ValueError, match="missing"):
        verify_bundle(tmp_path)


def _packet_candidates(tmp_path):
    bodies = {}
    candidates = []
    for i, color in enumerate(((200, 10, 10), (10, 200, 10), (10, 10, 200))):
        body = _img(color)
        path = tmp_path / f"cand-{i}.png"
        path.write_bytes(body)
        bodies[f"cand-{i}"] = path
        candidates.append(
            dict(
                candidate_id=f"cand-{i}",
                strategy="spark_det" if i else "random_det",
                family="brightness_shift",
                candidate_sha256=f"{i:064x}",
                image_sha256=sha256(body).hexdigest(),
                evidence_sha256="e" * 64,
                original_verdict="VALID",
                task_question="How many containers are visible?",
                expected_count=3,
                stable=i == 0,
                showcase=False,
            )
        )
    return candidates, bodies


def test_blinded_packet_hash_bound_and_private_mapping_separate(tmp_path):
    candidates, bodies = _packet_candidates(tmp_path)
    sample = select_sample(candidates, 7)
    packet = tmp_path / "packet"
    private = tmp_path / "private.json"
    manifest = build_packet(sample, candidates, bodies, packet, private)
    verified = verify_packet(packet)
    assert verified["sha256"] == manifest["sha256"]
    assert (packet / "images").exists()
    assert not list(packet.rglob("*private*"))
    private_text = private.read_text()
    assert "spark_det" in private_text
    for entry in manifest["entries"]:
        assert "strategy" not in entry and "family" not in entry
        assert (
            sha256((packet / entry["image_file"]).read_bytes()).hexdigest() == entry["image_sha256"]
        )
    # Review form must not leak strategy either.
    assert "spark_det" not in (packet / "review-form.json").read_text()
    assert "spark_det" not in (packet / "packet-manifest.json").read_text()
    # Blinded task context lets the reviewer judge count without strategy.
    assert all(
        e["task_question"] == "How many containers are visible?" and e["expected_count"] == 3
        for e in manifest["entries"]
    )
    assert "observed_count" in (packet / "review-form.json").read_text()


def test_protocol_cap_aligns_with_limits_and_policy_exposed():
    from evaluations.protocol import freeze_protocol

    base = dict(
        revision="test",
        dataset_sha256="a" * 64,
        policy_revision="strict-binary-human-gates-1",
        target_revision="b" * 64,
        code_revision="0a3ab3d",
        random_seed=71,
        target_calls_per_arm=500,
        max_candidates_per_arm=3,
        max_api_calls=300,
        max_usd="1.00",
        max_runtime_seconds=1200,
    )
    assert freeze_protocol(base).primary_policy == "FEEDBACK_V1"
    assert freeze_protocol({**base, "primary_policy": "ONE_SHOT"}).primary_policy == "ONE_SHOT"
    with pytest.raises(ValueError):
        freeze_protocol({**base, "target_calls_per_arm": 501})
    with pytest.raises(ValueError):
        freeze_protocol({**base, "primary_policy": "ADAPTIVE_V9"})


def test_packet_rejects_traversal_decode_private_inside_and_overwrite(tmp_path):
    candidates, bodies = _packet_candidates(tmp_path)
    sample = select_sample(candidates, 7)
    # Unsafe candidate ID.
    evil = [dict(c, candidate_id="../evil") if i == 0 else c for i, c in enumerate(candidates)]
    evil_sample = select_sample(evil, 7)
    with pytest.raises(ValueError, match="Unsafe"):
        build_packet(evil_sample, evil, bodies, tmp_path / "pe", tmp_path / "prive.json")
    # Hash-correct but undecodable image.
    fake = tmp_path / "fake.png"
    fake.write_bytes(b"not-an-image")
    bad_candidates = [dict(c) for c in candidates]
    bad_candidates[0]["image_sha256"] = sha256(b"not-an-image").hexdigest()
    bad_bodies = dict(bodies)
    bad_bodies["cand-0"] = fake
    bad_sample = select_sample(bad_candidates, 7)
    with pytest.raises(ValueError, match="decode|format|hash"):
        build_packet(
            bad_sample, bad_candidates, bad_bodies, tmp_path / "pf", tmp_path / "privf.json"
        )
    # Private mapping inside packet dir leaks.
    with pytest.raises(ValueError, match="outside"):
        build_packet(sample, candidates, bodies, tmp_path / "pg", tmp_path / "pg" / "private.json")
    # Immutable: second build over a reviewed packet refused.
    build_packet(sample, candidates, bodies, tmp_path / "ph", tmp_path / "privh.json")
    with pytest.raises(ValueError, match="immutable"):
        build_packet(sample, candidates, bodies, tmp_path / "ph", tmp_path / "privh.json")
    # Comparison must match caller-supplied source-evidence hash.
    with pytest.raises(ValueError, match="source-evidence"):
        build_packet(
            sample,
            candidates,
            bodies,
            tmp_path / "pi",
            tmp_path / "privi.json",
            comparison_media={"cand-0": (bodies["cand-0"], "0" * 64)},
        )


def test_truncated_jpeg_fails_full_bounded_decode(tmp_path):
    out = BytesIO()
    Image.new("RGB", (32, 32), (120, 40, 200)).save(out, format="JPEG")
    body = out.getvalue()
    candidates, bodies = _packet_candidates(tmp_path)
    for cut in (2, 10, 20):
        truncated = body[:-cut]
        path = tmp_path / f"trunc-{cut}.jpg"
        path.write_bytes(truncated)
        bad = [dict(c) for c in candidates]
        bad[0]["image_sha256"] = sha256(truncated).hexdigest()
        media = dict(bodies)
        media["cand-0"] = path
        sample = select_sample(bad, 7)
        with pytest.raises(ValueError, match="decode"):
            build_packet(sample, bad, media, tmp_path / f"pt{cut}", tmp_path / f"privt{cut}.json")
    # Intact JPEG passes and packet keeps the original bytes hash-bound.
    good = tmp_path / "good.jpg"
    good.write_bytes(body)
    ok = [dict(c) for c in candidates]
    ok[0]["image_sha256"] = sha256(body).hexdigest()
    media = dict(bodies)
    media["cand-0"] = good
    sample = select_sample(ok, 7)
    packet = tmp_path / "pjpg"
    build_packet(sample, ok, media, packet, tmp_path / "privjpg.json")
    entry = next(e for e in verify_packet(packet)["entries"] if e["candidate_id"] == "cand-0")
    assert (packet / entry["image_file"]).read_bytes() == body


def test_packet_requires_context_new_dir_and_exact_members(tmp_path):
    candidates, bodies = _packet_candidates(tmp_path)
    sample = select_sample(candidates, 7)
    # Missing blinded task context is not a usable audit packet.
    bare = [dict(c) for c in candidates]
    for c in bare:
        c.pop("task_question")
        c.pop("expected_count")
    with pytest.raises(ValueError, match="task context"):
        build_packet(
            select_sample(bare, 7), bare, bodies, tmp_path / "pctx", tmp_path / "privctx.json"
        )
    packet = tmp_path / "packet"
    build_packet(sample, candidates, bodies, packet, tmp_path / "priv.json")
    verify_packet(packet)
    # An untracked strategy file inside the packet voids verification.
    (packet / "selection.json").write_text("{}")
    with pytest.raises(ValueError, match="unexpected"):
        verify_packet(packet)
    (packet / "selection.json").unlink()
    verify_packet(packet)
    # A packet directory must start new/empty.
    with pytest.raises(ValueError, match="immutable"):
        build_packet(sample, candidates, bodies, packet, tmp_path / "priv.json")


def test_cli_audit_export_rejects_packet_overlapping_selection(tmp_path):
    from evaluations.cli import main

    candidates, bodies = _packet_candidates(tmp_path)
    images = tmp_path / "images"
    images.mkdir()
    for cid, path in bodies.items():
        (images / f"{cid}.png").write_bytes(path.read_bytes())
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"candidates": candidates}))
    out = tmp_path / "out"
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "audit-export",
                str(source),
                "--seed",
                "7",
                "--output",
                str(out),
                "--images",
                str(images),
                "--packet-dir",
                str(out),
            ]
        )
    assert excinfo.value.code == 2
    assert not (out / "packet-manifest.json").exists()


GLOBAL_STOP = "GLOBAL_EVALUATION_ENVELOPE_EXPIRED"


def test_global_envelope_truncates_later_arm_after_all_baselines_correct():
    rows = _arm_source(invalid=0)
    rows["audits"] = [decision(c) for c in rows["candidates"] if c["original_verdict"] == "VALID"]
    # All baselines are CORRECT in the fixture; the global envelope then
    # truncates the later spark arm before its local caps are spent.
    rows["experiments"][1]["state"] = "BUDGET_EXHAUSTED"
    rows["experiments"][1]["stop_reason"] = GLOBAL_STOP
    result = summarize(rows)
    assert result["primary_comparison_valid"] is False
    assert result["primary_difference"]["difference"] is None
    assert result["primary_difference"]["ratio"] is None
    assert "methodology-1" in result["failed_interrupted_excluded"]
    group = result["matched_groups"][0]
    assert "methodology-1" in group["non_terminal_primary"]
    assert group["primary_complete"] is False
    assert any("global" in r.lower() for r in result["comparison_incomplete_reasons"])


def test_global_stop_after_review_wait_withholds_comparison():
    rows = _arm_source(invalid=0)
    rows["audits"] = [decision(c) for c in rows["candidates"] if c["original_verdict"] == "VALID"]
    # A seed parked in review resumes only to find the global runtime /
    # authorization expired; its durable record carries the global reason.
    rows["experiments"][0]["state"] = "BUDGET_EXHAUSTED"
    rows["experiments"][0]["stop_reason"] = GLOBAL_STOP
    result = summarize(rows)
    assert result["primary_comparison_valid"] is False
    assert result["primary_difference"] == dict(spark=1, random=0, difference=None, ratio=None) or (
        result["primary_difference"]["difference"] is None
        and result["primary_difference"]["ratio"] is None
    )
    assert "methodology-0" in result["failed_interrupted_excluded"]


def test_ordinary_local_cap_remains_legitimate_matched_completion():
    rows = _arm_source(invalid=0)
    rows["audits"] = [decision(c) for c in rows["candidates"] if c["original_verdict"] == "VALID"]
    rows["experiments"][1]["state"] = "BUDGET_EXHAUSTED"
    rows["experiments"][1]["stop_reason"] = "SEARCH_CAP_REACHED"
    result = summarize(rows)
    assert result["primary_comparison_valid"] is True
    assert result["primary_difference"]["difference"] == 0
    assert result["primary_difference"]["ratio"] == 1.0
    assert "methodology-1" not in result["failed_interrupted_excluded"]


def test_baseline_exclusion_drops_are_not_audit_holds():
    rows = _arm_source(invalid=0)
    # Break one baseline so the whole seed is excluded; no audits at all.
    baseline = next(
        o
        for o in rows["observations"]
        if o["experiment_id"] == "methodology-0" and o["phase"] == "SEED_BASELINE"
    )
    baseline["outcome"] = "INCORRECT"
    rows["audits"] = []
    result = summarize(rows)
    assert result["audit_summary"]["reviewed"] == 0
    assert result["audit_summary"]["holds"] == 0
    assert result["audit_summary"]["non_audit_drops"] >= 1
    assert result["audit_summary"]["exclusion_drops"] >= 1
    for arm in result["arms"]:
        if arm["stable_automatic"]:
            assert arm["audit_holds"] == 0
            assert arm["non_audit_drops"] == arm["stable_automatic"]
            assert arm["exclusion_drops"] == arm["stable_automatic"]


def test_audit_hold_only_when_non_audit_gates_pass():
    rows = _arm_source(invalid=0)
    rows["audits"] = []  # non-audit gates pass, independent audit never PASSes
    result = summarize(rows)
    holds = [a for a in result["arms"] if a["stable_automatic"]]
    assert holds and all(a["audit_holds"] == 1 for a in holds)
    assert all(a["non_audit_drops"] == 0 for a in holds)
    assert result["audit_summary"]["holds"] == sum(a["stable_automatic"] for a in holds)
    assert result["audit_summary"]["non_audit_drops"] == 0


def test_verify_packet_rejects_truncated_comparison_with_resealed_hash(tmp_path):
    candidates, bodies = _packet_candidates(tmp_path)
    sample = select_sample(candidates, 7)
    out = BytesIO()
    Image.new("RGB", (32, 32), (120, 40, 200)).save(out, format="JPEG")
    good = out.getvalue()
    comp_path = tmp_path / "comp.jpg"
    comp_path.write_bytes(good)
    packet = tmp_path / "packet-comp"
    manifest = build_packet(
        sample,
        candidates,
        bodies,
        packet,
        tmp_path / "priv-comp.json",
        comparison_media={"cand-0": (comp_path, sha256(good).hexdigest())},
    )
    assert verify_packet(packet)["sha256"] == manifest["sha256"]
    # Truncate the comparison, recompute its hash and reseal the manifest:
    # hash/membership/blinding all pass, only a full bounded decode can fail.
    truncated = good[:-10]
    entry = next(e for e in manifest["entries"] if e["candidate_id"] == "cand-0")
    (packet / entry["comparison_file"]).write_bytes(truncated)
    tampered = json.loads((packet / "packet-manifest.json").read_bytes())
    for e in tampered["entries"]:
        if e["candidate_id"] == "cand-0":
            e["comparison_sha256"] = sha256(truncated).hexdigest()
    (packet / "packet-manifest.json").write_text(json.dumps(seal(tampered)))
    with pytest.raises(ValueError, match="decode"):
        verify_packet(packet)


def test_packet_wrong_or_missing_image_fails_closed(tmp_path):
    candidates, bodies = _packet_candidates(tmp_path)
    sample = select_sample(candidates, 7)
    wrong = dict(bodies)
    bad = tmp_path / "wrong.png"
    bad.write_bytes(_img((1, 2, 3)))
    wrong["cand-0"] = bad
    with pytest.raises(ValueError, match="hash mismatch"):
        build_packet(sample, candidates, wrong, tmp_path / "p1", tmp_path / "priv1.json")
    missing = {k: v for k, v in bodies.items() if k != "cand-1"}
    with pytest.raises(ValueError, match="Missing"):
        build_packet(sample, candidates, missing, tmp_path / "p2", tmp_path / "priv2.json")
