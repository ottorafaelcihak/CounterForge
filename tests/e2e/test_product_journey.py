"""Phase09 private owner journeys, local fixtures only; no live provider credentials."""

import asyncio
import copy
import os
import re
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from services.api.app.experiments import control
from services.api.app.service import Service
from services.worker.app.experiments import execute_experiment
from services.worker.app.fixtures import RECTANGLES
from tests.e2e.test_mutation_review import prepare_candidate
from tests.e2e.test_regression_export import prepare_stable
from tests.integrations.test_experiment_pipeline import replies

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_COUNTERFORGE_E2E") != "1", reason="isolated browser gate closed"
    ),
    pytest.mark.allow_hosts(["127.0.0.1", "localhost", "::1"]),
]


def sign_in(page, stack):
    page.get_by_label("Owner access token", exact=True).fill(stack.owner)
    page.get_by_role("button", name="Open workspace", exact=True).click()


def test_canonical_setup_to_reviewed_finding_and_export(setup_stack, monkeypatch):
    """Real setup/start/baseline, then explicit synthetic wrong replies for export coverage.

    Test-only in-process fixture stages replace the four final target replies. No
    runtime provider/worker behavior is changed and these are not model findings.
    """
    stack = setup_stack
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        foreign = []

        def local_only(route):
            if route.request.url.startswith(stack.origin + "/"):
                route.continue_()
            else:
                foreign.append(route.request.url)
                route.abort()

        page.route("**/*", local_only)
        try:
            page.goto(stack.origin + "/projects")
            sign_in(page, stack)
            expect(page.get_by_text("No projects yet", exact=True)).to_be_visible()
            page.get_by_role("link", name="New project", exact=True).click()
            page.wait_for_url("**/projects/new")
            expect(
                page.get_by_role("heading", name="New visual test project", exact=True)
            ).to_be_visible()
            page.get_by_label("Project name", exact=True).fill("Phase09 fixture journey")
            page.get_by_label(
                "I will use rights-cleared images and authorized targets for benign counting tests."
            ).check()
            page.get_by_role("button", name="Create project", exact=True).click()
            page.wait_for_url(re.compile(r"/projects/[0-9a-f-]+$"))
            project_id = page.url.rsplit("/", 1)[-1]
            page.get_by_role("link", name="Add source seed", exact=True).click()
            page.wait_for_url("**/seeds/new")
            page.get_by_role("button", name="Load development seed", exact=True).click()
            expect(page.get_by_text("Selected five-blue-containers.png")).to_be_visible()
            page.get_by_label("Label reviewer", exact=True).fill("Simulated browser review")
            page.get_by_label(
                "I have rights to use this image and have verified its count."
            ).check()
            page.get_by_role("button", name="Upload seed", exact=True).click()
            masks = page.get_by_role("link", name="Review masks", exact=True)
            expect(masks).to_be_visible()
            seed_id = masks.get_attribute("href").split("/")[2]
            page.get_by_role("link", name="Create visual test contract", exact=True).click()
            expect(page.get_by_role("button", name="Create contract", exact=True)).to_be_disabled()
            page.get_by_role("link", name="Review or correct source masks", exact=True).click()
            # A meaningful non-default label, set before the first mask is
            # saved; it must survive save/approve, a mask-page reload, and
            # appear locked on the contract-only route (F1 regression).
            page.get_by_label("Object label", exact=True).fill("blue storage object")
            for ordinal, (x1, y1, x2, y2) in enumerate(RECTANGLES):
                if ordinal:
                    page.get_by_role("button", name="Add missing instance", exact=True).click()
                for key, value in {
                    "left": x1,
                    "top": y1,
                    "width": x2 - x1,
                    "height": y2 - y1,
                }.items():
                    page.get_by_label("Rectangle " + key, exact=True).fill(str(value))
                page.get_by_role("button", name="Apply rectangle", exact=True).click()
                page.get_by_label("Correction or review notes", exact=True).fill(
                    f"Simulated fixture mask for container {ordinal + 1}."
                )
                page.get_by_role("button", name="Save new mask", exact=True).click()
                expect(page.locator(".instance")).to_have_count(ordinal + 1)
                page.get_by_label("Correction or review notes", exact=True).fill(
                    f"Simulated exact revision approval for container {ordinal + 1}."
                )
                page.get_by_role("button", name="Approve this revision", exact=True).click()
                expect(page.locator(".instance").last).to_contain_text("Reviewed")
            # Reload the mask page: the loaded label must hydrate from the
            # stored masks, not fall back to the default.
            page.goto(stack.origin + f"/seeds/{seed_id}/masks")
            expect(page.get_by_label("Object label", exact=True)).to_have_value(
                "blue storage object"
            )
            page.goto(stack.origin + f"/seeds/{seed_id}/contracts/new")
            protected_label = page.get_by_label("Protected object label", exact=True)
            expect(protected_label).to_have_value("blue storage object")
            expect(protected_label).to_have_attribute("readonly", "")
            expect(page.get_by_role("button", name="Create contract", exact=True)).to_be_enabled()
            page.get_by_label("Candidates", exact=True).fill("1")
            page.get_by_label("Target calls", exact=True).fill("7")
            page.get_by_role("button", name="Create contract", exact=True).click()
            expect(page.get_by_text("Contract version 1 saved", exact=True)).to_be_visible()
            page.goto(stack.origin + f"/projects/{project_id}/targets")
            page.get_by_label("Target model", exact=True).select_option("LOCAL_FIXTURE")
            page.get_by_role("button", name="Save target", exact=True).click()
            expect(page.get_by_label("Saved target", exact=True)).not_to_have_value("")
            page.get_by_role("link", name="Project overview", exact=True).click()
            start = page.get_by_role("button", name="Start bounded experiment", exact=True)
            expect(start).to_be_enabled()
            start.click()
            page.wait_for_url(re.compile(r"/experiments/[0-9a-f-]+$"))
            eid = UUID(page.url.rsplit("/", 1)[-1])
            expect(page.get_by_test_id("experiment-state")).to_have_text("PAUSED", timeout=45000)
            expect(page.get_by_test_id("baseline-progress")).to_have_text("Seed baseline: 3 / 3")
            page.get_by_role("link", name="Review candidate", exact=True).first.click()
            page.get_by_label("Semantic integrity", exact=True).select_option("consistent")
            page.get_by_label("Visibility", exact=True).select_option("clear")
            page.get_by_label("Review notes", exact=True).fill(
                "Simulated fixture candidate review."
            )
            page.get_by_role("button", name="Record review", exact=True).click()
            expect(page.get_by_test_id("candidate-verdict")).to_have_text("VALID")
            cfg = stack.settings.model_copy(update={"enqueue_jobs": False})
            with stack.factory() as uow:
                control(Service(uow, cfg), eid, "resume")
                uow.commit()

            async def finish_fixture():
                replies(monkeypatch, [4, 4, 4, 4])
                for _ in range(12):
                    await execute_experiment(stack.factory, cfg, eid)
                    with stack.factory() as uow:
                        state = Service(uow, cfg).experiment(eid)["state"]
                        if state in {"COMPLETED", "BUDGET_EXHAUSTED"}:
                            break

            # Playwright's sync API owns this thread's event loop; run the
            # synthetic dispatch on a separate thread with its own loop.
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, finish_fixture()).result()
            with stack.factory() as uow:
                finding_id = uow.connection.scalar(
                    select(uow.tables.findings.c.id).where(
                        uow.tables.findings.c.experiment_id == eid
                    )
                )
                assert finding_id
                record = Service(uow, cfg).finding(finding_id)
                assert record["status"] == "STABLE"
            page.goto(stack.origin + "/experiments/" + str(eid))
            expect(page.get_by_test_id("baseline-progress")).to_have_text("Seed baseline: 3 / 3")
            expect(page.get_by_test_id("target-call-count")).to_have_text("7 / 7")
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(stack.tmp_path / "phase09-cockpit-1600x900.png"))
            page.goto(stack.origin + "/findings/" + str(finding_id))
            expect(page.get_by_test_id("request-export")).to_be_enabled()
            page.get_by_label("Show protected mask overlay", exact=True).check()
            page.get_by_label("Show pixel difference", exact=True).check()
            expect(page.locator(".protected-mask-overlay")).to_be_visible()
            expect(
                page.get_by_role("link", name="Download original evidence", exact=True)
            ).to_be_visible()
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(stack.tmp_path / "phase09-finding-1600x900.png"))
            page.get_by_test_id("request-export").click()
            expect(page.get_by_test_id("export-state")).to_have_text("REGISTERED", timeout=45000)
            page.get_by_test_id("verify-export").click()
            expect(page.get_by_test_id("download-export")).to_be_visible()
            with page.expect_download() as downloaded:
                page.get_by_test_id("download-export").click()
            assert downloaded.value.failure() is None
            page.goto(stack.origin + "/projects")
            summary = page.get_by_test_id("run-summary").first
            expect(summary).to_contain_text("Stable findings")
            expect(summary).to_contain_text("7 / 7")
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(stack.tmp_path / "phase09-project-mobile.png"), full_page=True)
            assert not foreign and stack.owner not in page.content()
            with stack.factory() as uow:
                events = uow.connection.execute(select(uow.tables.usage_events)).mappings().all()
                # Zero-paid gate: only dispatched/settled rows count as spend.
                # Undispatched holds/voids may carry a null actual and local
                # CPU/fixture providers; diagnostics list row tuples only.
                rows = [
                    (
                        e["provider"],
                        e["category"],
                        e["status"],
                        e["dispatched_at"] is not None,
                        float(e["actual_usd"] or 0),
                    )
                    for e in events
                ]
                assert rows, "expected retained usage rows for the fixture journey"
                assert all(row[0] in {"FIXTURE", "LOCAL", "BATCH_HOLD"} for row in rows), rows
                dispatched = [
                    row for row in rows if row[3] or row[2] in {"SETTLED", "UNKNOWN_CHARGE"}
                ]
                assert dispatched and all(
                    row[0] == "FIXTURE" and row[4] == 0 for row in dispatched
                ), rows
                assert sum(row[4] for row in rows) == 0, rows
                assert not any(row[2] == "UNKNOWN_CHARGE" for row in rows), rows
        except Exception:
            page.screenshot(path=str(stack.tmp_path / "phase09-failure.png"), full_page=True)
            print("Phase09 visible errors:", page.get_by_role("alert").all_text_contents())
            raise
        finally:
            context.close()
            browser.close()


def test_dropped_replayed_stream_auth_expiry_and_keyboard_focus(setup_stack, vtc_payload):
    stack = setup_stack
    mid, _ = prepare_candidate(stack, vtc_payload)
    with stack.factory() as uow:
        s = Service(uow, stack.settings)
        eid = s.candidates.row(mid)["experiment_id"]
        saved = s.experiment_dto(s.experiment(eid)).model_dump(mode="json")
    saved["state"] = "EVALUATING"
    saved["metrics"]["total_target_calls"] = 7
    renewed, streams, urls = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 900})
        page = context.new_page()
        endpoint = stack.origin + "/api/backend/experiments/" + str(eid)

        def snapshot(route):
            record = copy.deepcopy(saved)
            if renewed:
                record["state"] = "COMPLETED"
                record["metrics"]["total_target_calls"] = 9
            route.fulfill(json=record)

        def stream(route):
            urls.append(route.request.url)
            streams.append(route.request.headers.get("last-event-id"))
            if len(streams) == 1:
                frame = f'id: 900\ndata: {{"id":900,"experiment_id":"{eid}","event":"fixture_replay","payload":{{}}}}\n\n'
                route.fulfill(content_type="text/event-stream", body=frame * 2)
            else:
                route.fulfill(
                    status=401,
                    json={
                        "error": {
                            "message": "Session expired.",
                            "correlation_id": "browser-stream-expired",
                        }
                    },
                )

        page.route(endpoint, snapshot)
        page.route(endpoint + "/events", stream)
        try:
            page.goto(stack.origin + "/experiments/" + str(eid))
            sign_in(page, stack)
            reload_button = page.get_by_role("button", name="Reload experiment", exact=True)
            expect(page.get_by_test_id("target-call-count")).to_have_text("7 / 60")
            reload_button.focus()
            dialog = page.get_by_role("dialog", name="Renew owner session")
            expect(dialog).to_be_visible(timeout=15000)
            token = dialog.get_by_label("Owner access token", exact=True)
            expect(token).to_be_focused()
            page.keyboard.press("Shift+Tab")
            expect(
                dialog.get_by_role("button", name="Continue without signing in", exact=True)
            ).to_be_focused()
            page.keyboard.press("Tab")
            expect(token).to_be_focused()
            page.keyboard.press("Escape")
            expect(dialog).not_to_be_visible()
            expect(page.get_by_text("Session expired.", exact=False)).to_be_visible()
            page.get_by_role("button", name="Renew session", exact=True).click()
            expect(dialog).to_be_visible()
            expect(token).to_be_focused()
            dialog.get_by_role("button", name="Continue without signing in", exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(page.get_by_role("button", name="Renew session", exact=True)).to_be_visible()
            page.get_by_role("button", name="Renew session", exact=True).click()
            expect(dialog).to_be_visible()
            expect(token).to_be_focused()
            assert streams == ["0", "900"]
            assert all("?" not in url and stack.owner not in url for url in urls)
            assert page.get_by_test_id("experiment-event").count() == 1
            expect(page.get_by_test_id("experiment-state")).to_have_text("REFRESHING_SAVED_STATE")
            renewed.append(True)
            token.fill(stack.owner)
            dialog.get_by_role("button", name="Open workspace", exact=True).click()
            expect(dialog).not_to_be_visible()
            expect(page.get_by_test_id("experiment-state")).to_have_text("COMPLETED")
            expect(page.get_by_test_id("target-call-count")).to_have_text("9 / 60")
            expect(reload_button).to_be_focused()
            expect(page.get_by_role("button", name="Resume", exact=True)).to_be_disabled()
            page.screenshot(path=str(stack.tmp_path / "phase09-reconnect-contract-1600x900.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert len(streams) == 2
        except Exception:
            page.screenshot(path=str(stack.tmp_path / "phase09-failure.png"), full_page=True)
            print("Phase09 visible errors:", page.get_by_role("alert").all_text_contents())
            raise
        finally:
            context.close()
            browser.close()


def test_paginated_rejection_filters_uncertainty_downloads_and_failed_export(
    setup_stack, vtc_payload, monkeypatch
):
    stack = setup_stack
    fid, mid = prepare_stable(stack, vtc_payload, monkeypatch)
    with stack.factory() as uow:
        s = Service(uow, stack.settings)
        candidate = s.candidates.dto(s.candidates.row(mid)).model_dump(mode="json")
    items = []
    for index, verdict in enumerate(["INVALID", "REVIEW_REQUIRED", "VALID"]):
        item = copy.deepcopy(candidate)
        item["id"] = str(UUID(int=index + 1))
        item["validation"]["verdict"] = verdict
        # The copied candidate retains UNKNOWN checks from the real fixture
        # run; reset every check so only the intended one drives filtering.
        for check in item["validation"]["checks"]:
            check["status"] = "PASS"
        item["validation"]["checks"][0]["status"] = (
            "UNKNOWN" if index == 1 else "FAIL" if index == 0 else "PASS"
        )
        items.append(item)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        path = stack.origin + "/api/backend/experiments/" + candidate["experiment_id"]

        def paged(route):
            second = "cursor=" in route.request.url
            route.fulfill(
                json={
                    "items": items[1:] if second else items[:1],
                    "next_cursor": None if second else "fixture-page-two",
                }
            )

        page.route(path + "/mutations?*", paged)
        try:
            page.goto(stack.origin + "/experiments/" + candidate["experiment_id"] + "/mutations")
            sign_in(page, stack)
            expect(page.get_by_test_id("candidate-card")).to_have_count(3)
            page.get_by_label("Validity filter", exact=True).select_option("INVALID")
            expect(page.get_by_test_id("candidate-card")).to_have_count(1)
            expect(page.get_by_test_id("candidate-card")).to_contain_text("FAIL")
            page.get_by_label("Validity filter", exact=True).select_option("UNKNOWN")
            expect(page.get_by_test_id("candidate-card")).to_have_count(1)
            expect(page.get_by_test_id("candidate-card")).to_contain_text("UNKNOWN")
            page.get_by_label("Validity filter", exact=True).select_option("REVIEW_REQUIRED")
            expect(page.get_by_test_id("candidate-filter-count")).to_contain_text("Showing 1 of 3")
            page.route(
                "**/api/backend/findings/*/exports",
                lambda route: route.fulfill(
                    status=503,
                    json={
                        "error": {
                            "message": "Fixture export unavailable. Retry the same request.",
                            "correlation_id": "fixture-export-09",
                        }
                    },
                ),
            )
            page.goto(stack.origin + "/findings/" + str(fid))
            page.get_by_test_id("request-export").click()
            expect(
                page.get_by_role("alert").filter(has_text="Reference: fixture-export-09")
            ).to_contain_text("Reference: fixture-export-09")
            page.get_by_label("Show pixel difference", exact=True).check()
            expect(
                page.get_by_role("link", name="Download diff evidence", exact=True)
            ).to_be_visible()
            page.get_by_label("Show pixel difference", exact=True).uncheck()
            expect(
                page.get_by_role("link", name="Download diff evidence", exact=True)
            ).not_to_be_visible()
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        except Exception:
            page.screenshot(path=str(stack.tmp_path / "phase09-filter-failure.png"), full_page=True)
            print("Phase09 visible errors:", page.get_by_role("alert").all_text_contents())
            raise
        finally:
            browser.close()
