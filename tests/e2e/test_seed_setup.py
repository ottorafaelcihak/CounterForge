"""One complete owner flow through Next, FastAPI, PostgreSQL, Redis and real RQ."""

import os
import re
from uuid import UUID

import pytest
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_COUNTERFORGE_E2E") != "1",
        reason="explicit isolated browser gate is closed",
    ),
    pytest.mark.allow_hosts(["127.0.0.1", "localhost", "::1"]),
]


def test_fixture_seed_setup_and_audited_mask_correction(setup_stack):
    stack = setup_stack
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        try:
            foreign = []

            def local_only(route):
                if route.request.url.startswith(stack.origin + "/"):
                    route.continue_()
                else:
                    foreign.append(route.request.url)
                    route.abort()

            page.route("**/*", local_only)
            page.goto(stack.origin)
            assert page.request.get(stack.origin + "/api/backend/projects").status == 401
            bad_login = page.request.post(
                stack.origin + "/api/session",
                headers={"Origin": stack.origin},
                data={"token": "wrong-owner-token"},
            )
            assert bad_login.status == 401 and "set-cookie" not in bad_login.headers
            assert (
                page.request.post(
                    stack.origin + "/api/session",
                    headers={"Origin": "https://foreign.invalid"},
                    data={"token": "wrong-owner-token"},
                ).status
                == 403
            )
            page.get_by_label("Owner access token").fill(stack.owner)
            page.get_by_role("button", name="Open workspace").click()
            expect(page.get_by_role("heading", name="Your visual test projects")).to_be_visible()
            cookie = next(
                item for item in context.cookies() if item["name"] == "counterforge_owner"
            )
            assert (
                cookie["httpOnly"]
                and cookie["sameSite"] == "Strict"
                and stack.owner not in cookie["value"]
            )
            assert stack.owner not in page.content()
            assert (
                page.request.get(stack.origin + "/api/backend/https/foreign_invalid").status == 404
            )
            assert page.evaluate("JSON.stringify({...localStorage})") == "{}"
            denied = page.request.post(
                stack.origin + "/api/backend/projects",
                headers={"Origin": "https://foreign.invalid"},
                data={"name": "Unwanted project"},
            )
            assert denied.status == 403

            # Follow a second cursor page rather than silently losing item 101.
            def project_pages(route):
                second = "cursor=" in route.request.url
                indices = [101] if second else range(1, 101)
                route.fulfill(
                    json={
                        "items": [
                            {
                                "id": str(UUID(int=i)),
                                "name": f"Paged project {i}",
                                "description": "Synthetic pagination check",
                                "task_type": "object_counting",
                                "created_at": "2026-10-28T00:00:00Z",
                            }
                            for i in indices
                        ],
                        "next_cursor": None if second else "second-page",
                    }
                )

            page.route("**/api/backend/projects?*", project_pages)
            page.reload()
            expect(page.locator(".project-list li")).to_have_count(101)
            page.unroute("**/api/backend/projects?*", project_pages)
            page.reload()
            expect(page.locator(".project-list li")).to_have_count(0)
            page.get_by_label("Project name", exact=True).fill("Five container regression")
            page.get_by_label("Description", exact=True).fill(
                "Isolated synthetic browser acceptance flow."
            )
            page.get_by_role("button", name="Create project", exact=True).click()
            page.wait_for_url(re.compile("/projects/[0-9a-f-]+$"))
            expect(page.get_by_role("heading", name="Five container regression")).to_be_visible()
            page.get_by_role("button", name="Load development seed").click()
            expect(page.get_by_text("Selected five-blue-containers.png")).to_be_visible()
            page.get_by_label("Label reviewer", exact=True).fill("Browser human fixture reviewer")
            page.get_by_label(
                "I have rights to use this image and have verified its count."
            ).check()
            page.get_by_role("button", name="Upload seed", exact=True).click()
            expect(page.get_by_role("link", name="Review masks")).to_be_visible()
            page.get_by_label("Target model", exact=True).select_option("LOCAL_FIXTURE")
            page.get_by_role("button", name="Save target", exact=True).click()
            page.get_by_role("button", name="Check target", exact=True).click()
            expect(
                page.get_by_text("Target returned 5. Matches the human count.", exact=True)
            ).to_be_visible(timeout=30000)
            page.get_by_role("link", name="Review masks").click()
            page.wait_for_url(re.compile("/seeds/[0-9a-f-]+/masks$"))
            seed_id = page.url.split("/")[-2]
            page.get_by_role("button", name="Request segmentation", exact=True).click()
            expect(page.locator(".instance")).to_have_count(4, timeout=30000)
            expect(
                page.get_by_text("Detection count differs from your human count.", exact=True)
            ).to_be_visible()
            expect(page.get_by_role("button", name="Create contract", exact=True)).to_be_disabled()
            state = page.request.get(
                stack.origin + f"/api/backend/seeds/{seed_id}/mask-review"
            ).json()
            assert (state["detected_count"], state["expected_count"], state["draft_count"]) == (
                4,
                5,
                4,
            )
            assert state["synthetic"] and not state["can_create_contract"]
            page.get_by_role("button", name="Add missing instance", exact=True).click()
            expect(
                page.get_by_role("heading", name="New instance mask", exact=True)
            ).to_be_visible()
            for key, value in {"left": 392, "top": 60, "width": 56, "height": 116}.items():
                page.get_by_label("Rectangle " + key, exact=True).fill(str(value))
            page.get_by_role("button", name="Apply rectangle", exact=True).click()
            page.get_by_label("Correction or review notes", exact=True).fill(
                "Added the fifth container missed by the synthetic segmentation."
            )
            page.get_by_role("button", name="Save new mask", exact=True).click()
            expect(page.locator(".instance")).to_have_count(5)
            for ordinal in range(1, 6):
                page.locator(".instance").filter(
                    has=page.locator("span", has_text=re.compile(f"^Mask {ordinal}$"))
                ).click()
                page.get_by_label("Correction or review notes", exact=True).fill(
                    f"Reviewed the pixels of distinct container {ordinal}."
                )
                page.get_by_role("button", name="Approve this revision", exact=True).click()
                expect(
                    page.locator(".instance").filter(has_text=f"Mask {ordinal}")
                ).to_contain_text("Reviewed")
            expect(page.get_by_role("button", name="Create contract", exact=True)).to_be_disabled()
            before = page.request.get(
                stack.origin + f"/api/backend/seeds/{seed_id}/masks?limit=100"
            ).json()["items"]
            first = next(mask for mask in before if mask["object_ordinal"] == 0)
            page.locator(".instance").filter(
                has=page.locator("span", has_text=re.compile("^Mask 1$"))
            ).click()
            page.get_by_role("button", name="Erase", exact=True).click()
            drawing = page.get_by_role("img", name="Source image with instance mask overlays")
            expect(drawing).to_be_visible()
            box = drawing.bounding_box()
            assert box

            def point(source_x, source_y):
                return box["x"] + box["width"] * source_x / 480, box["y"] + box[
                    "height"
                ] * source_y / 240

            # Erasing background is a no-op, preserving review and undo capacity.
            page.mouse.click(*point(15, 15))
            expect(page.get_by_text("Unsaved mask correction", exact=True)).not_to_be_visible()
            expect(page.get_by_role("button", name="Undo stroke", exact=True)).to_be_disabled()
            page.get_by_role("button", name="Paint", exact=True).click()
            page.mouse.move(*point(110, 25))
            page.mouse.down()
            page.mouse.move(*point(200, 45), steps=1)
            page.mouse.up()
            expect(page.get_by_text("Unsaved mask correction", exact=True)).to_be_visible()
            midpoint = drawing.evaluate(
                "node => Array.from(node.getContext('2d').getImageData(155, 35, 1, 1).data)"
            )
            assert midpoint[0] < 180, "A fast stroke must also paint its midpoint"
            current_url = page.url
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.get_by_role("link", name="Project setup", exact=True).click()
            assert page.url == current_url
            page.once("dialog", lambda dialog: dialog.dismiss())
            page.get_by_role("button", name="Sign out", exact=True).click()
            expect(page.get_by_text("Unsaved mask correction", exact=True)).to_be_visible()
            page.once("dialog", lambda dialog: dialog.dismiss())
            try:
                page.go_back(timeout=2000)
            except BrowserError:
                pass
            expect(page).to_have_url(current_url)
            expect(page.get_by_text("Unsaved mask correction", exact=True)).to_be_visible()
            # Renew an expired session without unmounting the in-progress raster.
            context.clear_cookies()
            page.get_by_role("button", name="Revision history", exact=True).click()
            expect(page.get_by_role("dialog", name="Renew owner session")).to_be_visible()
            page.get_by_label("Owner access token", exact=True).fill(stack.owner)
            page.get_by_role("button", name="Open workspace", exact=True).click()
            expect(page.get_by_role("dialog", name="Renew owner session")).not_to_be_visible()
            expect(page.get_by_text("Unsaved mask correction", exact=True)).to_be_visible()
            page.get_by_role("button", name="Undo stroke", exact=True).click()
            expect(page.get_by_text("Unsaved mask correction", exact=True)).not_to_be_visible()
            expect(page.get_by_role("button", name="Undo stroke", exact=True)).to_be_disabled()
            page.get_by_role("button", name="Erase", exact=True).click()
            drawing.scroll_into_view_if_needed()
            box = drawing.bounding_box()
            assert box
            x, y = box["x"] + box["width"] * 55 / 480, box["y"] + box["height"] * 85 / 240
            page.mouse.move(x, y)
            page.mouse.down()
            page.mouse.move(x + 3, y + 3)
            page.mouse.up()
            expect(page.get_by_text("Unsaved mask correction", exact=True)).to_be_visible()
            page.get_by_label("Correction or review notes", exact=True).fill(
                "Brush-erased a small patch while preserving this distinct container."
            )
            page.get_by_role("button", name="Save correction", exact=True).click()
            expect(
                page.get_by_text(
                    "Mask saved as revision 2. Review this exact revision before approving.",
                    exact=True,
                )
            ).to_be_visible()
            page.get_by_role("button", name="Revision history", exact=True).click()
            expect(
                page.get_by_role("heading", name="Immutable revision history", exact=True)
            ).to_be_visible()
            history = page.request.get(
                stack.origin + f"/api/backend/seeds/{seed_id}/masks/{first['id']}/revisions"
            ).json()["items"]
            assert [item["revision"] for item in history] == [1, 2]
            assert history[0]["reviewed"] and history[0]["mask_asset_id"] == first["mask_asset_id"]
            assert (
                not history[1]["reviewed"] and history[1]["mask_asset_id"] != first["mask_asset_id"]
            )
            assert (
                page.request.get(
                    stack.origin + f"/api/backend/assets/{first['mask_asset_id']}/content"
                ).status
                == 200
            )
            page.get_by_label("Correction or review notes", exact=True).fill(
                "Reviewed the exact corrected second revision of container one."
            )
            page.get_by_role("button", name="Approve this revision", exact=True).click()
            page.get_by_label("Discrepancy review notes", exact=True).fill(
                "Corrected the missed fifth object and reviewed all five current masks."
            )
            page.get_by_role("button", name="Acknowledge reviewed mask set", exact=True).click()
            expect(page.get_by_role("button", name="Create contract", exact=True)).to_be_enabled()
            page.get_by_role("button", name="Create contract", exact=True).click()
            expect(page.get_by_text("Contract version 1 saved", exact=True)).to_be_visible()
            source = page.request.get(stack.origin + f"/api/backend/seeds/{seed_id}").json()
            assert source["expected"]["value"] == 5
            with stack.factory() as uow:
                tables = uow.tables
                contract = uow.connection.execute(select(tables.visual_contracts)).mappings().one()
                assert contract["contract_json"]["expected"]["value"] == 5
                assert len(contract["contract_json"]["protected"]["mask_asset_ids"]) == 5
                evidence = uow.connection.execute(select(tables.usage_events)).mappings().all()
                assert evidence and all(
                    item["actual_usd"] == 0 and item["provider"] == "FIXTURE" for item in evidence
                )
                actions = set(uow.connection.scalars(select(tables.audit_events.c.action)))
                assert {
                    "MASK_REVISED",
                    "MASK_DISCREPANCY_ACKNOWLEDGED",
                    "CONTRACT_CREATED",
                } <= actions
            screenshot = stack.tmp_path / "review-desktop.png"
            page.screenshot(path=str(screenshot), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(stack.tmp_path / "review-mobile.png"), full_page=True)
            assert not foreign, foreign
            page.get_by_role("button", name="Sign out", exact=True).click()
            expect(page.get_by_label("Owner access token")).to_be_visible()
            assert (
                page.request.get(
                    stack.origin + f"/api/backend/assets/{first['mask_asset_id']}/content"
                ).status
                == 401
            )
        except BaseException:
            page.screenshot(path=str(stack.tmp_path / "failure.png"), full_page=True)
            print("Browser failure screenshot:", stack.tmp_path / "failure.png")
            print("Current page:", page.url)
            print("Visible form labels:", page.locator("label").all_text_contents())
            raise
        finally:
            context.close()
            browser.close()
