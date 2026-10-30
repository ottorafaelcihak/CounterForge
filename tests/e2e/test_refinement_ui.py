"""Real-process refinement controls; all approvals/outcomes are simulated fixtures."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.requests import CreateMutation, MutationReviewRequest, RetestRequest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from services.api.app.refinements import request_retest
from services.api.app.service import Service
from services.worker.app.experiments import execute_experiment
from services.worker.app.jobs import execute_job
from tests.e2e.test_mutation_review import prepare_candidate
from tests.e2e.test_regression_export import prepare_stable
from tests.integrations.test_candidates import review_body
from tests.integrations.test_experiment_pipeline import replies

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_COUNTERFORGE_E2E") != "1", reason="isolated browser gate closed"
    ),
    pytest.mark.allow_hosts(["127.0.0.1", "localhost", "::1"]),
]


def login(page, stack, path):
    page.goto(stack.origin + path)
    page.get_by_label("Owner access token").fill(stack.owner)
    page.get_by_role("button", name="Open workspace", exact=True).click()
    expect(page.get_by_role("heading", name="Finding evidence", exact=True)).to_be_visible()


def complete_source(stack, mid):
    with stack.factory() as uow:
        s = Service(uow, stack.settings)
        eid = s.candidates.row(mid)["experiment_id"]
        uow.connection.execute(
            s.t.experiments.update()
            .where(s.t.experiments.c.id == eid)
            .values(state="COMPLETED", runner_token=None, dispatch_pending=False)
        )
        uow.commit()


def brightness_source(stack, payload, monkeypatch):
    old, _ = prepare_candidate(stack, payload)
    with stack.factory() as uow:
        s = Service(uow, stack.settings)
        prior = s.candidates.dto(s.candidates.row(old))
        s.candidates.review(
            old,
            MutationReviewRequest.model_validate(
                review_body(
                    prior.model_dump(mode="json"),
                    semantic="additional_object",
                    notes="Simulated fixture rejection to isolate brightness.",
                )
            ),
        )
        eid = s.candidates.row(old)["experiment_id"]
        child = s.candidates.create(
            eid,
            CreateMutation(
                plan=MutationPlan(
                    family="brightness_shift",
                    strength=0,
                    hypothesis="Synthetic brightness failure",
                    prompt="Preserve all five objects",
                    requires_muse_image=False,
                    estimated_cost_usd=0,
                ),
                rng_seed=42,
            ),
            "brightness-browser",
        )
        s.candidates.review(
            child.id,
            MutationReviewRequest.model_validate(
                review_body(
                    child.model_dump(mode="json"),
                    notes="Automated LOCAL_FIXTURE simulated approval.",
                )
            ),
        )
        uow.connection.execute(
            s.t.experiments.update()
            .where(s.t.experiments.c.id == eid)
            .values(state="BASELINE_VALIDATING")
        )
        uow.commit()
    cfg = stack.settings.model_copy(update={"enqueue_jobs": False})

    async def run():
        replies(monkeypatch, [5, 5, 5])
        for _ in range(3):
            await execute_experiment(stack.factory, cfg, eid)
        with stack.factory() as uow:
            uow.connection.execute(
                uow.tables.experiments.update()
                .where(uow.tables.experiments.c.id == eid)
                .values(state="EVALUATING")
            )
            uow.commit()
        replies(monkeypatch, [4, 4, 4, 4])
        for _ in range(4):
            await execute_experiment(stack.factory, cfg, eid)

    asyncio.run(run())
    complete_source(stack, child.id)
    with stack.factory() as uow:
        fid = uow.connection.scalar(
            select(uow.tables.findings.c.id).where(uow.tables.findings.c.mutation_id == child.id)
        )
        assert Service(uow, cfg).finding(fid)["status"] == "STABLE"
    return fid, child.id


def test_brightness_passing_boundary_keeps_original_and_review_gate(
    setup_stack, vtc_payload, monkeypatch
):
    stack = setup_stack
    fid, mid = brightness_source(stack, vtc_payload, monkeypatch)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 390, "height": 844})
        try:
            login(page, stack, "/findings/" + str(fid))
            page.get_by_text("Find a simpler validated failure", exact=True).click()
            page.get_by_label("Maximum minimization probes").fill("1")
            page.get_by_test_id("start-minimization").click()
            expect(page.get_by_test_id("resume-minimization")).to_be_visible(timeout=45000)
            page.get_by_test_id("resume-minimization").click()
            expect(page.locator(".minimization-panel [role=alert]")).to_contain_text("VALID")
            page.get_by_role("link", name="Review probe candidate", exact=True).click()
            expect(page.get_by_role("link", name="Parent candidate", exact=True)).to_have_attribute(
                "href", "/mutations/" + str(mid)
            )
            page.get_by_label("Semantic integrity", exact=True).select_option("consistent")
            page.get_by_label("Visibility", exact=True).select_option("clear")
            page.get_by_label("Review notes", exact=True).fill(
                "Automated LOCAL_FIXTURE simulated independent probe review."
            )
            page.get_by_role("button", name="Record review", exact=True).click()
            expect(page.get_by_test_id("candidate-verdict")).to_have_text("VALID")
            page.get_by_role("link", name="Return to finding refinement", exact=True).click()
            page.get_by_text("Find a simpler validated failure", exact=True).click()
            page.get_by_test_id("resume-minimization").click()
            expect(page.get_by_test_id("minimization-probe")).to_contain_text(
                "PASSING", timeout=45000
            )
            expect(
                page.get_by_text(
                    "A simpler passing neighbor is retained as boundary evidence.", exact=True
                )
            ).to_be_visible()
            expect(
                page.get_by_role("link", name="View original finding", exact=True)
            ).to_have_attribute("href", "/findings/" + str(fid))
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_test_id("minimization-result").scroll_into_view_if_needed()
            page.screenshot(path=str(stack.tmp_path / "phase07-boundary-mobile.png"))
        finally:
            browser.close()


def test_unsupported_refinement_and_fresh_retests_preserve_bundle(
    setup_stack, vtc_payload, monkeypatch
):
    stack = setup_stack
    fid, mid = prepare_stable(stack, vtc_payload, monkeypatch)
    complete_source(stack, mid)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        try:
            login(page, stack, "/findings/" + str(fid))
            page.get_by_text("Find a simpler validated failure", exact=True).click()
            page.get_by_test_id("start-minimization").click()
            expect(page.get_by_test_id("minimization-result")).to_have_attribute(
                "data-stop-reason", "UNSUPPORTED_FAMILY", timeout=45000
            )
            expect(
                page.get_by_text(
                    "The original finding is retained; no stable reduction has replaced it.",
                    exact=True,
                )
            ).to_be_visible()
            page.get_by_test_id("request-export").click()
            expect(page.get_by_test_id("export-state")).to_have_text("REGISTERED", timeout=45000)
            page.get_by_text("Compare a target configuration · fresh retest", exact=True).click()
            expect(page.get_by_test_id("start-retest")).to_be_disabled()
            page.get_by_test_id("verify-export").click()
            expect(page.get_by_test_id("export-state")).to_have_text("READY")
            xid = page.get_by_test_id("export-record").get_attribute("data-export-id")
            endpoint = stack.origin + "/api/backend/exports/" + xid + "/download"
            original = page.request.get(endpoint).body()
            for n in (1, 2):
                page.get_by_test_id("start-retest").click()
                expect(
                    page.locator('[data-testid="retest-result"][data-classification="FIXED"]')
                ).to_have_count(n, timeout=45000)
            ids = page.get_by_test_id("retest-job").evaluate_all(
                "items => items.map(i => i.dataset.jobId)"
            )
            assert len(ids) == len(set(ids)) == 2
            page.get_by_label("Maximum retest calls").fill("1")
            page.get_by_test_id("start-retest").click()
            partial = page.locator(
                '[data-testid="retest-result"][data-classification="INCONCLUSIVE"]'
            )
            expect(partial).to_have_count(1, timeout=45000)
            expect(partial).to_contain_text("Fresh coverage 1/3")
            expect(partial).to_contain_text("BUDGET EXCEEDED")
            assert page.request.get(endpoint).body() == original
            assert (
                page.request.post(
                    stack.origin + "/api/backend/regressions/" + xid + "/retest",
                    headers={"Origin": "https://foreign.invalid"},
                ).status
                == 403
            )
            assert (
                page.request.get(
                    stack.origin + "/api/backend/regressions/" + xid + "/retests?token=forbidden"
                ).status
                == 422
            )
            page.set_viewport_size({"width": 390, "height": 844})
            partial.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(stack.tmp_path / "phase07-retest-mobile.png"))
            # Retain an UNKNOWN fixture job independently; it must render as uncertainty.
            with stack.factory() as uow:
                s = Service(uow, stack.settings.model_copy(update={"enqueue_jobs": False}))
                tid = s.experiment(s.candidates.row(mid)["experiment_id"])["target_config_id"]
                job = request_retest(
                    s,
                    UUID(xid),
                    RetestRequest(
                        target_config_id=tid,
                        limits={"max_calls": 3, "max_estimated_usd": 1, "max_runtime_seconds": 600},
                    ),
                    "unknown-browser-fixture",
                )
                uow.commit()
            replies(monkeypatch, ["UNKNOWN", 5, 5])
            # Playwright's sync driver already owns this thread's event loop.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(
                    lambda: asyncio.run(
                        execute_job(
                            stack.factory,
                            stack.settings.model_copy(update={"enqueue_jobs": False}),
                            job.job_id,
                        )
                    )
                ).result(timeout=30)
            unknown = page.get_by_test_id("retest-job").filter(has_text=job.job_id.hex[:8])
            expect(unknown).to_contain_text("Uncertain replies: 1/3", timeout=15000)
            expect(unknown).to_contain_text("2 interpretable replies")
            expect(unknown.locator('[data-testid="retest-result"]')).to_have_attribute(
                "data-classification", "INCONCLUSIVE"
            )
            assert page.request.get(endpoint).body() == original
            page.get_by_role("button", name="Sign out", exact=True).click()
            expect(page.get_by_label("Owner access token")).to_be_visible()
            for path in (
                "findings/" + str(fid) + "/minimizations",
                "regressions/" + xid + "/retests",
                "jobs/" + str(job.job_id),
            ):
                assert page.request.get(stack.origin + "/api/backend/" + path).status == 401
        finally:
            browser.close()
