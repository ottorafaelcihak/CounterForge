"""Authenticated finding-to-ZIP flow with explicit simulated fixture evidence."""

import asyncio
import io
import os
from zipfile import ZipFile

import pytest
from counterforge_contracts.requests import MutationReviewRequest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from services.api.app.service import Service
from services.worker.app.experiments import execute_experiment
from tests.e2e.test_mutation_review import prepare_candidate
from tests.integrations.test_candidates import review_body
from tests.integrations.test_experiment_pipeline import replies

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_COUNTERFORGE_E2E") != "1", reason="isolated browser gate closed"
    ),
    pytest.mark.allow_hosts(["127.0.0.1", "localhost", "::1"]),
]


def prepare_stable(stack, payload, monkeypatch):
    mid, _ = prepare_candidate(stack, payload)
    with stack.factory() as uow:
        s = Service(uow, stack.settings)
        m = s.candidates.dto(s.candidates.row(mid))
        s.candidates.review(
            mid,
            MutationReviewRequest.model_validate(
                review_body(
                    m.model_dump(mode="json"),
                    notes="Automated LOCAL_FIXTURE simulated candidate approval.",
                )
            ),
        )
        eid = s.candidates.row(mid)["experiment_id"]
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
    with stack.factory() as uow:
        fid = uow.connection.scalar(select(uow.tables.findings.c.id))
        assert Service(uow, stack.settings).finding(fid)["status"] == "STABLE"
    return fid, mid


def test_fixture_finding_export_download_and_revocation(setup_stack, vtc_payload, monkeypatch):
    stack = setup_stack
    fid, mid = prepare_stable(stack, vtc_payload, monkeypatch)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100}, accept_downloads=True
        )
        page = context.new_page()
        foreign, payloads = [], []

        def local_only(route):
            if route.request.url.startswith(stack.origin + "/"):
                route.continue_()
            else:
                foreign.append(route.request.url)
                route.abort()

        def capture(response):
            if "/api/backend/" in response.url and "application/json" in response.headers.get(
                "content-type", ""
            ):
                payloads.append(response.text())

        page.route("**/*", local_only)
        page.on("response", capture)
        try:
            page.goto(stack.origin + "/findings/" + str(fid))
            page.get_by_label("Owner access token").fill(stack.owner)
            page.get_by_role("button", name="Open workspace", exact=True).click()
            expect(page.get_by_role("heading", name="Finding evidence", exact=True)).to_be_visible()
            expect(
                page.get_by_text("LOCAL_FIXTURE · simulated evidence", exact=False).first
            ).to_be_visible()
            expect(page.get_by_text("Count 5 · background_replacement", exact=True)).to_be_visible()
            expect(page.locator(".observation-card")).to_have_count(7)
            expect(page.get_by_test_id("request-export")).to_be_enabled()
            page.get_by_test_id("request-export").click()
            expect(page.get_by_test_id("export-state")).to_have_text("REGISTERED", timeout=45000)
            page.get_by_test_id("verify-export").click()
            expect(page.get_by_test_id("export-state")).to_have_text("READY")
            expect(page.get_by_test_id("bundle-sha256")).to_have_text(
                __import__("re").compile("^[0-9a-f]{64}$")
            )
            xid = page.get_by_test_id("export-record").get_attribute("data-export-id")
            with page.expect_download() as download_event:
                page.get_by_test_id("download-export").click()
            download = download_event.value
            assert download.failure() is None
            data = download.path().read_bytes()
            with ZipFile(io.BytesIO(data)) as archive:
                assert "sha256sums.txt" in archive.namelist()
                assert b'"provenance":"LOCAL_FIXTURE"' in archive.read("manifest.json")
                assert stack.owner.encode() not in b"".join(
                    archive.read(name) for name in archive.namelist()
                )
            endpoint = stack.origin + "/api/backend/exports/" + xid
            assert page.request.get(endpoint + "/download?token=forbidden").status == 422
            assert (
                page.request.post(
                    stack.origin + "/api/backend/findings/" + str(fid) + "/exports",
                    headers={"Origin": "https://foreign.invalid"},
                ).status
                == 403
            )
            assert (
                page.request.get(stack.origin + "/api/backend/exports/" + xid + "/private").status
                == 404
            )
            page.get_by_role("link", name="Regression library", exact=True).click()
            page.wait_for_url("**/regressions")
            expect(
                page.get_by_role("heading", name="Regression library", exact=True)
            ).to_be_visible()
            expect(page.get_by_test_id("export-state")).to_have_text("REGISTERED")
            page.get_by_test_id("verify-export").click()
            expect(page.get_by_test_id("export-state")).to_have_text("READY")
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(
                path=str(stack.tmp_path / "phase06-library-desktop.png"), full_page=True
            )
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(stack.tmp_path / "phase06-library-mobile.png"), full_page=True)
            page.goto(stack.origin + "/findings/" + str(fid))
            expect(page.get_by_test_id("export-state")).to_have_text("REGISTERED")
            page.get_by_test_id("verify-export").click()
            expect(page.get_by_test_id("export-state")).to_have_text("READY")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(stack.tmp_path / "phase06-finding-mobile.png"), full_page=True)
            with stack.factory() as uow:
                s = Service(uow, stack.settings)
                current = s.candidates.dto(s.candidates.row(mid)).model_dump(mode="json")
                s.candidates.review(
                    mid,
                    MutationReviewRequest.model_validate(
                        review_body(
                            current,
                            semantic="additional_object",
                            notes="Automated fixture revocation test.",
                        )
                    ),
                )
                uow.commit()
            page.get_by_role("button", name="Reload evidence", exact=True).click()
            expect(page.get_by_test_id("export-state")).to_have_text("REVOKED", timeout=10000)
            expect(page.get_by_test_id("download-export")).to_have_count(0)
            assert page.request.get(endpoint + "/download").status == 409
            assert not foreign
            assert all(
                term not in text
                for text in payloads
                for term in (
                    stack.owner,
                    '"storage_key"',
                    '"provider_request_id"',
                    '"api_key"',
                    '"operation_key"',
                )
            )
            page.get_by_role("button", name="Sign out", exact=True).click()
            expect(page.get_by_label("Owner access token")).to_be_visible()
            for path in (
                "findings/" + str(fid),
                "regressions",
                "exports/" + xid,
                "exports/" + xid + "/download",
            ):
                assert page.request.get(stack.origin + "/api/backend/" + path).status == 401
        except Exception:
            page.screenshot(path=str(stack.tmp_path / "phase06-export-failure.png"), full_page=True)
            raise
        finally:
            context.close()
            browser.close()


def test_failed_export_requires_explicit_new_attempt(setup_stack, vtc_payload, monkeypatch):
    stack = setup_stack
    fid, _ = prepare_stable(stack, vtc_payload, monkeypatch)
    with stack.factory() as uow:
        s = Service(uow, stack.settings)
        failed = s.request_export(fid, "simulated-failed-export")
        s.db.execute(
            s.t.background_jobs.update()
            .where(s.t.background_jobs.c.id == failed.export_id)
            .values(state="FAILED", error_code="INVALID_INPUT")
        )
        uow.commit()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(stack.origin + "/findings/" + str(fid))
            page.get_by_label("Owner access token").fill(stack.owner)
            page.get_by_role("button", name="Open workspace", exact=True).click()
            expect(page.get_by_test_id("export-state")).to_have_text("FAILED")
            expect(page.get_by_test_id("request-export")).to_be_disabled()
            page.get_by_role(
                "button", name="Retry failed export as a new attempt", exact=True
            ).click()
            expect(page.get_by_test_id("export-state").filter(has_text="REGISTERED")).to_have_count(
                1, timeout=45000
            )
            ids = page.get_by_test_id("export-record").evaluate_all(
                "items => items.map(i => i.dataset.exportId)"
            )
            assert len(ids) == 2 and str(failed.export_id) in ids and len(set(ids)) == 2
        finally:
            browser.close()
