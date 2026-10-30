"""Anonymous /demo browser checks: empty default and labeled fixture snapshot.

Evidence is synthesized fixture provenance with an explicit fixture audit
actor inserted directly into the disposable stack database. No live
providers, retained private assets or real publication approval involved.
"""

import os
from datetime import UTC, datetime

import pytest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import func, select

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_COUNTERFORGE_E2E") != "1", reason="isolated browser gate closed"
    ),
    pytest.mark.allow_hosts(["127.0.0.1", "localhost", "::1"]),
]


def insert_fixture_snapshot(stack, payload, monkeypatch) -> dict[str, str]:
    from counterforge_contracts.requests import DemoPublicationRequest

    from services.api.app.service import Service
    from tests.e2e.test_regression_export import prepare_stable

    fid, mid = prepare_stable(stack, payload, monkeypatch)
    with stack.factory() as uow:
        service = Service(uow, stack.settings)
        usage_count = uow.connection.scalar(
            select(func.count()).select_from(uow.tables.usage_events)
        )
        job_count = uow.connection.scalar(
            select(func.count()).select_from(uow.tables.background_jobs)
        )
        assert usage_count >= 7 and job_count >= 1
        finding = service.finding(fid)
        mutation = service.candidates.row(mid)
        report = service.candidates.checked_report(mutation)
        uow.connection.execute(
            uow.tables.findings.update()
            .where(uow.tables.findings.c.id == fid)
            .values(
                demo_approved_by="fixture-reviewer",
                demo_approved_at=datetime.now(UTC),
                demo_validation_sha256=report.report_sha256,
            )
        )
        from services.api.app import public_demo

        snapshot = public_demo.prepare_snapshot(service, finding["experiment_id"])
        public_demo.publish_snapshot(
            service,
            snapshot.id,
            DemoPublicationRequest(
                publication_intent="Fixture browser gate only; not a real publication.",
                confirm_publication=True,
                redistribution_rights_acknowledged=True,
                manifest_sha256=snapshot.manifest_sha256,
            ),
        )
        manifest = public_demo.public_manifest(service)
        seed_id = service.experiment(finding["experiment_id"])["config_snapshot"]["seed"][
            "image_asset_id"
        ]
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.usage_events))
            == usage_count
        )
        assert (
            uow.connection.scalar(select(func.count()).select_from(uow.tables.background_jobs))
            == job_count
        )
        uow.commit()
    return {
        "usage_count": str(usage_count),
        "job_count": str(job_count),
        "snapshot_id": str(snapshot.id),
        "seed_asset_id": str(seed_id),
        "mutated_asset_id": str(mutation["image_asset_id"]),
        "public_asset_id": str(manifest.assets[0].id),
    }


def test_demo_empty_anonymous_without_owner_shell(setup_stack):
    stack = setup_stack
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        try:
            page.goto(stack.origin + "/demo")
            expect(page.get_by_role("heading", name="Recorded visual-test demo")).to_be_visible()
            expect(page.get_by_text("Local fixture preview", exact=False).first).to_be_visible()
            # No owner sign-in gate or credential field on the public route.
            expect(page.get_by_label("Owner access token")).to_have_count(0)
            assert stack.owner not in page.content()
        finally:
            browser.close()


def test_demo_fixture_snapshot_anonymous_gallery(setup_stack, vtc_payload, monkeypatch):
    stack = setup_stack
    ids = insert_fixture_snapshot(stack, vtc_payload, monkeypatch)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        try:
            page.goto(stack.origin + "/demo?experiment_id=" + ids["seed_asset_id"])
            expect(page.get_by_text("Local fixture preview", exact=False).first).to_be_visible()
            expect(page.get_by_text("Stable finding", exact=False).first).to_be_visible()
            expect(
                page.get_by_role("note").get_by_text(
                    "not evidence of live foundation-model failures", exact=False
                )
            ).to_be_visible()
            expect(page).to_have_url(stack.origin + "/demo")
            images = page.locator(".demo-finding img")
            expect(images).to_have_count(2)
            assert page.evaluate(
                "[...document.querySelectorAll('.demo-finding img')].every(i => i.complete && i.naturalWidth > 0)"
            )
            assert stack.owner not in page.content()
            assert ids["seed_asset_id"] not in page.content()
            # Guessed private IDs stay denied through the public proxy.
            denied = context.request.get(
                stack.origin + "/api/public/demo/assets/" + ids["seed_asset_id"] + "/content"
            )
            assert denied.status == 404
            served = context.request.get(
                stack.origin + "/api/public/demo/assets/" + ids["public_asset_id"] + "/content"
            )
            assert served.status == 200
            with stack.factory() as uow:
                assert uow.connection.scalar(
                    select(func.count()).select_from(uow.tables.usage_events)
                ) == int(ids["usage_count"])
                assert uow.connection.scalar(
                    select(func.count()).select_from(uow.tables.background_jobs)
                ) == int(ids["job_count"])
        finally:
            context.close()
            browser.close()
