"""Real-process feedback UI and review gates, with explicit simulated approvals."""

import os

import pytest
from playwright.sync_api import expect, sync_playwright

from services.api.app.service import Service
from tests.e2e.test_mutation_review import prepare_candidate

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_COUNTERFORGE_E2E") != "1", reason="isolated browser gate closed"
    ),
    pytest.mark.allow_hosts(["127.0.0.1", "localhost", "::1"]),
]


def test_feedback_opt_in_actual_history_review_gates_and_mobile(setup_stack, vtc_payload):
    stack = setup_stack
    mid, _ = prepare_candidate(stack, vtc_payload)
    with stack.factory() as uow:
        service = Service(uow, stack.settings)
        project_id = service.experiment(service.candidates.row(mid)["experiment_id"])["project_id"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        foreign, starts = [], []

        def local_only(route):
            if route.request.url.startswith(stack.origin + "/"):
                route.continue_()
            else:
                foreign.append(route.request.url)
                route.abort()

        def record_start(route):
            body = route.request.post_data_json
            assert body["search_policy"] == "FEEDBACK_V1"
            assert body["random_seed"] == 20261030
            response = route.fetch()
            assert response.status == 202, response.text()
            starts.append(response.json()["experiment_id"])
            route.fulfill(response=response)

        page.route("**/*", local_only)
        page.route("**/api/backend/experiments", record_start)
        try:
            page.goto(stack.origin)
            page.get_by_label("Owner access token").fill(stack.owner)
            page.get_by_role("button", name="Open workspace", exact=True).click()
            page.goto(stack.origin + "/projects/" + str(project_id))
            policy = page.get_by_label("Search policy", exact=True)
            expect(policy).to_have_value("ONE_SHOT")
            policy.select_option("FEEDBACK_V1")
            page.get_by_label("Recorded random seed", exact=True).fill("20261030")
            page.get_by_label("Maximum candidates", exact=True).fill("2")
            page.get_by_role("button", name="Start bounded experiment", exact=True).click()
            page.wait_for_url("**/experiments/*")
            expect(page.get_by_test_id("experiment-state")).to_have_text("PAUSED", timeout=45000)
            expect(
                page.get_by_role("heading", name="Feedback planning evidence", exact=True)
            ).to_be_visible()
            expect(
                page.get_by_text("Attempt 1 · random exploration · RANDOM EXPLORATION", exact=True)
            ).to_be_visible()

            for attempt in range(2):
                pending = page.locator(".candidate-run-list article").filter(
                    has_text="REVIEW_REQUIRED"
                )
                expect(pending).to_have_count(1, timeout=45000)
                pending.get_by_role("link", name="Review candidate", exact=True).click()
                page.get_by_label("Semantic integrity", exact=True).select_option("consistent")
                page.get_by_label("Visibility", exact=True).select_option("clear")
                page.get_by_label("Review notes", exact=True).fill(
                    "Automated LOCAL_FIXTURE: simulated independent review for feedback test."
                )
                page.get_by_role("button", name="Record review", exact=True).click()
                expect(page.get_by_test_id("candidate-verdict")).to_have_text("VALID")
                page.get_by_role("link", name="Experiment", exact=True).click()
                page.get_by_role("button", name="Resume", exact=True).click()
                if attempt == 0:
                    second = page.locator(".planning-history > details.form-panel").nth(1)
                    expect(second.locator("summary").first).to_contain_text(
                        "Attempt 2", timeout=45000
                    )
                    second.locator("summary").first.click()
                    second.get_by_text("Outcomes supplied to this batch (1)", exact=True).click()
                    expect(second.get_by_text("SCREENING: CORRECT", exact=True)).to_be_visible()
                    expect(page.get_by_test_id("experiment-state")).to_have_text(
                        "PAUSED", timeout=45000
                    )
            expect(page.get_by_test_id("experiment-state")).to_have_text("COMPLETED", timeout=45000)
            expect(page.get_by_test_id("target-call-count")).to_contain_text("5 /")
            assert len(starts) == 1
            page.set_viewport_size({"width": 390, "height": 844})
            second = page.locator(".planning-history > details.form-panel").nth(1)
            second.locator("summary").first.click()
            second.scroll_into_view_if_needed()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(stack.tmp_path / "phase07-feedback-mobile-panel.png"))
            page.screenshot(
                path=str(stack.tmp_path / "phase07-feedback-mobile.png"), full_page=True
            )
            assert not foreign
        except Exception:
            page.screenshot(
                path=str(stack.tmp_path / "phase07-feedback-failure.png"), full_page=True
            )
            raise
        finally:
            browser.close()
