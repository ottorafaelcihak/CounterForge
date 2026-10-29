"""Real-process bounded fixture experiment; all human decisions are simulated."""

import os
from uuid import UUID

import pytest
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from services.api.app.service import Service
from tests.e2e.test_mutation_review import prepare_candidate

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_COUNTERFORGE_E2E") != "1", reason="isolated browser gate closed"
    ),
    pytest.mark.allow_hosts(["127.0.0.1", "localhost", "::1"]),
]


def test_fixture_experiment_start_retry_review_resume_and_private_events(setup_stack, vtc_payload):
    stack = setup_stack
    existing_id, _ = prepare_candidate(stack, vtc_payload)
    with stack.factory() as uow:
        s = Service(uow, stack.settings)
        prior = s.experiment(s.candidates.row(existing_id)["experiment_id"])
        project_id = prior["project_id"]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
        page = context.new_page()
        foreign, first_start, failed_requests = [], [], []
        page.on(
            "response",
            lambda response: (
                failed_requests.append((response.status, response.url.split("/api/backend/")[-1]))
                if response.status >= 400
                else None
            ),
        )

        def local_only(route):
            if route.request.url.startswith(stack.origin + "/"):
                route.continue_()
            else:
                foreign.append(route.request.url)
                route.abort()

        def lost_start_reply(route):
            response = route.fetch()
            if not first_start:
                assert response.status == 202
                first_start.append(response.json()["experiment_id"])
                route.fulfill(
                    status=503, json={"error": {"message": "Simulated lost start reply."}}
                )
            else:
                assert response.json()["experiment_id"] == first_start[0]
                route.fulfill(response=response)

        page.route("**/*", local_only)
        page.route("**/api/backend/experiments", lost_start_reply)
        try:
            page.goto(stack.origin)
            page.get_by_label("Owner access token").fill(stack.owner)
            page.get_by_role("button", name="Open workspace").click()
            page.goto(stack.origin + "/projects/" + str(project_id))
            start = page.get_by_role("button", name="Start bounded experiment", exact=True)
            expect(start).to_be_enabled()
            start.click()
            expect(page.get_by_text("Simulated lost start reply.", exact=True)).to_be_visible()
            start.click()
            page.wait_for_url("**/experiments/" + first_start[0])
            expect(
                page.get_by_role("heading", name="Experiment cockpit", exact=True)
            ).to_be_visible()
            state = page.get_by_test_id("experiment-state")
            expect(state).to_have_text("PAUSED", timeout=45000)
            expect(page.get_by_test_id("baseline-progress")).to_contain_text("3 / 3")
            expect(page.get_by_test_id("target-call-count")).to_contain_text("3")
            expect(page.get_by_text("Review candidates to continue.", exact=True)).to_be_visible()
            page.get_by_role("link", name="Review candidate", exact=True).first.click()
            expect(page.get_by_test_id("human-count")).to_have_text("5")
            page.get_by_label("Semantic integrity", exact=True).select_option("consistent")
            page.get_by_label("Visibility", exact=True).select_option("clear")
            page.get_by_label("Review notes", exact=True).fill(
                "Automated fixture: simulated candidate approval for bounded loop."
            )
            page.get_by_role("button", name="Record review", exact=True).click()
            expect(page.get_by_test_id("candidate-verdict")).to_have_text("VALID")
            page.get_by_role("link", name="Experiment", exact=True).click()
            page.get_by_role("button", name="Resume", exact=True).click()
            expect(state).to_have_text("COMPLETED", timeout=45000)
            expect(page.get_by_test_id("target-call-count")).to_contain_text("4")
            expect(page.get_by_test_id("experiment-event").first).to_be_visible()
            expect(
                page.get_by_text("Saved events; experiment finished.", exact=True)
            ).to_be_visible()
            expect(page.get_by_test_id("committed-exposure")).to_have_text("$0.000000")
            expect(page.get_by_test_id("remaining-headroom")).to_contain_text(
                "$10.000000 remaining"
            )
            page.reload()
            expect(state).to_have_text("COMPLETED")
            expect(page.get_by_test_id("target-call-count")).to_contain_text("4")
            event_ids = page.get_by_test_id("experiment-event").evaluate_all(
                "items => items.map(i => i.dataset.eventId)"
            )
            assert len(event_ids) == len(set(event_ids))
            # The actual streaming proxy forwards the explicit persisted cursor.
            endpoint = stack.origin + "/api/backend/experiments/" + first_start[0]
            history = page.request.get(endpoint + "/event-history").json()
            last = history[-1]["id"]
            replay = page.request.get(
                endpoint + "/events", headers={"Last-Event-ID": str(last - 1)}
            )
            assert replay.status == 200 and f"id: {last}\n" in replay.text()
            assert all(
                int(line[4:]) > last - 1
                for line in replay.text().splitlines()
                if line.startswith("id: ")
            )
            assert page.request.get(endpoint + "/events?token=forbidden").status == 422
            page.evaluate("window.scrollTo(0,0)")
            page.screenshot(path=str(stack.tmp_path / "phase05-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(stack.tmp_path / "phase05-mobile.png"))
            assert not foreign and stack.owner not in page.content()
            page.get_by_role("button", name="Sign out", exact=True).click()
            expect(page.get_by_label("Owner access token")).to_be_visible()
            endpoint = stack.origin + "/api/backend/experiments/" + first_start[0]
            assert page.request.get(endpoint + "/events").status == 401
            with stack.factory() as uow:
                s = Service(uow, stack.settings)
                observations = (
                    s.db.execute(
                        select(s.t.model_observations).where(
                            s.t.model_observations.c.experiment_id == UUID(first_start[0])
                        )
                    )
                    .mappings()
                    .all()
                )
                assert [o["phase"] for o in observations].count("SEED_BASELINE") == 3
                assert [o["phase"] for o in observations].count("SCREENING") == 1
                assert s.experiment(UUID(first_start[0]))["calls_consumed"] == 4
        except Exception:
            page.screenshot(path=str(stack.tmp_path / "phase05-failure.png"), full_page=True)
            print("Failed application requests:", failed_requests)
            raise
        finally:
            context.close()
            browser.close()


def test_saved_controls_baseline_rejection_and_unresolved_display(setup_stack, vtc_payload):
    """Synthetic stored snapshots exercise UI decisions; they are not live model evidence."""
    from counterforge_contracts.requests import StartExperiment

    from services.api.app.experiments import transition

    stack = setup_stack
    candidate_id, _ = prepare_candidate(stack, vtc_payload)
    with stack.factory() as uow:
        service = Service(uow, stack.settings)
        old = service.experiment(service.candidates.row(candidate_id)["experiment_id"])
        eid = old["id"]
        rejected = service.start_experiment(
            StartExperiment(
                project_id=old["project_id"],
                contract_id=old["contract_id"],
                target_config_id=old["target_config_id"],
            ),
            "browser-rejected-snapshot",
        )
        transition(service, rejected.experiment_id, "SEED_INVALID_FOR_SEARCH", "BASELINE_INCORRECT")
        unresolved = service.start_experiment(
            StartExperiment(
                project_id=old["project_id"],
                contract_id=old["contract_id"],
                target_config_id=old["target_config_id"],
            ),
            "browser-unresolved-snapshot",
        )
        transition(
            service, unresolved.experiment_id, "PAUSED", "RECOVERY_PROVIDER_OUTCOME_REVIEW_REQUIRED"
        )
        uow.connection.execute(
            service.t.experiments.update()
            .where(service.t.experiments.c.id == unresolved.experiment_id)
            .values(checkpoint_json={"unresolved_result": True, "resume_state": "PLANNING"})
        )
        uow.commit()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(stack.origin)
            page.get_by_label("Owner access token").fill(stack.owner)
            page.get_by_role("button", name="Open workspace").click()
            page.goto(stack.origin + "/experiments/" + str(eid))
            page.get_by_role("button", name="Pause", exact=True).click()
            expect(page.get_by_test_id("experiment-state")).to_have_text("PAUSED")
            page.once("dialog", lambda dialog: dialog.accept())
            page.get_by_role("button", name="Cancel experiment", exact=True).click()
            expect(page.get_by_test_id("experiment-state")).to_have_text("CANCELLED")
            expect(page.get_by_role("button", name="Resume", exact=True)).to_be_disabled()
            page.goto(stack.origin + "/experiments/" + str(rejected.experiment_id))
            expect(page.get_by_test_id("experiment-state")).to_have_text("SEED_INVALID_FOR_SEARCH")
            expect(page.get_by_test_id("target-call-count")).to_contain_text("0 / 60")
            expect(page.get_by_text("Saved reason: BASELINE_INCORRECT", exact=True)).to_be_visible()
            page.goto(stack.origin + "/experiments/" + str(unresolved.experiment_id))
            expect(page.get_by_role("button", name="Resume", exact=True)).to_be_disabled()
            expect(
                page.get_by_text(
                    "Resolve retained provider outcomes or unknown charges before resuming.",
                    exact=True,
                )
            ).to_be_visible()
        finally:
            browser.close()


def test_real_fixture_budget_exhaustion_in_cockpit(setup_stack, vtc_payload):
    stack = setup_stack
    candidate_id, _ = prepare_candidate(stack, vtc_payload)
    with stack.factory() as uow:
        service = Service(uow, stack.settings)
        project_id = service.experiment(service.candidates.row(candidate_id)["experiment_id"])[
            "project_id"
        ]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(stack.origin)
            page.get_by_label("Owner access token").fill(stack.owner)
            page.get_by_role("button", name="Open workspace").click()
            page.goto(stack.origin + "/projects/" + str(project_id))
            expect(
                page.get_by_role("button", name="Start bounded experiment", exact=True)
            ).to_be_enabled()
            page.get_by_label("Maximum target calls", exact=True).fill("3")
            page.get_by_label("Maximum run dollars", exact=True).fill("1")
            # Selecting the same contract must preserve the operator's tighter dollars.
            current = page.get_by_label("Reviewed contract", exact=True).input_value()
            page.get_by_label("Reviewed contract", exact=True).select_option(current)
            expect(page.get_by_label("Maximum run dollars", exact=True)).to_have_value("1")
            page.get_by_role("button", name="Start bounded experiment", exact=True).click()
            page.wait_for_url("**/experiments/*")
            expect(page.get_by_test_id("experiment-state")).to_have_text(
                "BUDGET_EXHAUSTED", timeout=45000
            )
            expect(page.get_by_test_id("baseline-progress")).to_contain_text("3 / 3")
            expect(page.get_by_test_id("target-call-count")).to_have_text("3 / 3")
            expect(page.get_by_test_id("remaining-headroom")).to_contain_text("$1.000000 remaining")
            expect(page.get_by_text("Saved reason: SEARCH_CAP_REACHED", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Resume", exact=True)).to_be_disabled()
        finally:
            browser.close()
