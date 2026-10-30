"""Real-process candidate evidence/review UI; all human decisions are simulated test data."""

import io
import os
from datetime import UTC, datetime

import pytest
from counterforge_contracts.plans import MutationPlan
from counterforge_contracts.requests import (
    CreateMutation,
    CreateProject,
    CreateTargetConfig,
    MutationReviewRequest,
    StartExperiment,
)
from counterforge_contracts.vtc import VisualTestContract
from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

from services.api.app.masks import decode_mask
from services.api.app.service import Service
from services.worker.app.fixtures import RECTANGLES, seed_bytes

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("RUN_COUNTERFORGE_E2E") != "1",
        reason="explicit isolated browser gate is closed",
    ),
    pytest.mark.allow_hosts(["127.0.0.1", "localhost", "::1"]),
]


def prepare_candidate(stack, payload):
    """Provision only isolated fixture approvals; these are not real human sign-off."""
    with stack.factory() as uow:
        service = Service(uow, stack.settings)
        project = service.create_project(CreateProject(name="Candidate evidence fixture"))
        seed = service.create_seed(
            project.id,
            seed_bytes(),
            "image/png",
            question="How many blue containers are visible? Return only an integer.",
            expected_count=5,
            license_ack=True,
            label_reviewer="Automated fixture: simulated label review",
        )
        ids = []
        for ordinal, (x1, y1, x2, y2) in enumerate(RECTANGLES):
            raster = Image.new("L", (480, 240))
            ImageDraw.Draw(raster).rectangle((x1, y1, x2 - 1, y2 - 1), fill=255)
            stream = io.BytesIO()
            raster.save(stream, "PNG")
            mask = decode_mask(stream.getvalue(), width=480, height=240)
            asset = service.artifacts.put(
                project.id,
                mask.png,
                "MASK",
                "image/png",
                width=480,
                height=240,
                metadata={"synthetic": True, "fixture": "phase04-browser"},
            )
            uow.connection.execute(
                uow.tables.seed_masks.insert().values(
                    seed_id=seed.id,
                    mask_asset_id=asset.id,
                    object_label="blue container",
                    object_ordinal=ordinal,
                    geometry_json=mask.geometry(),
                    reviewed=True,
                    reviewer="Automated fixture: simulated mask review",
                    reviewed_at=datetime.now(UTC),
                    origin="FIXTURE",
                )
            )
            ids.append(str(asset.id))
        payload["case_id"] = seed.case_key
        payload["prompt"] = seed.question
        payload["expected"] = seed.expected.model_dump(mode="json")
        payload["protected"]["mask_asset_ids"] = ids
        contract = service.create_contract(seed.id, VisualTestContract.model_validate(payload))
        target = service.create_target(
            project.id,
            CreateTargetConfig(
                adapter_key="LOCAL_FIXTURE",
                model_id="LOCAL_FIXTURE",
                prompt_template="Count the blue containers. Return one integer.",
            ),
        )
        experiment = service.start_experiment(
            StartExperiment(
                project_id=project.id,
                contract_id=contract.id,
                target_config_id=target.id,
            ),
            "phase04-browser-experiment",
        )
        candidate = service.candidates.create(
            experiment.experiment_id,
            CreateMutation(
                plan=MutationPlan(
                    family="background_replacement",
                    strength=0.3,
                    hypothesis="Synthetic background evidence fixture",
                    prompt="Replace background only; preserve the five containers.",
                    requires_muse_image=False,
                    estimated_cost_usd=0,
                ),
                rng_seed=42,
            ),
            "phase04-browser-candidate",
        )
        uow.commit()
        return candidate.id, candidate.assets["original"]


def test_candidate_evidence_review_and_explicit_audit(setup_stack, vtc_payload):
    stack = setup_stack
    candidate_id, original_id = prepare_candidate(stack, vtc_payload)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100})
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
            page.goto(stack.origin)
            page.get_by_label("Owner access token").fill(stack.owner)
            page.get_by_role("button", name="Open workspace").click()
            expect(page.get_by_role("heading", name="Your visual test projects")).to_be_visible()
            page.goto(stack.origin + "/mutations/" + str(candidate_id))
            expect(
                page.get_by_role("heading", name="Candidate evidence", exact=True)
            ).to_be_visible()
            verdict = page.get_by_test_id("candidate-verdict")
            expect(verdict).to_have_text("REVIEW_REQUIRED")
            expect(page.get_by_test_id("human-count")).to_have_text("5")
            expect(page.get_by_test_id("evidence-image")).to_have_count(5)
            expect(page.get_by_test_id("validation-revision")).to_have_count(1)
            record = page.get_by_role("button", name="Record review", exact=True)
            endpoint = stack.origin + "/api/backend/mutations/" + str(candidate_id)
            assert page.request.get(endpoint + "?provider_url=untrusted").status == 422
            assert page.request.get(endpoint + "/segment").status == 404
            assert (
                page.request.post(
                    endpoint + "/review", headers={"Origin": "https://untrusted.invalid"}, data={}
                ).status
                == 403
            )

            def decide(semantic, visibility, note):
                page.get_by_label("Semantic integrity", exact=True).select_option(semantic)
                page.get_by_label("Visibility", exact=True).select_option(visibility)
                page.get_by_label("Review notes", exact=True).fill(note)

            # A missing comparison asset blocks decisions even if the fields
            # are complete; loading a replacement page clears that block.
            evidence = page.request.get(endpoint).json()
            missing = "**/api/backend/assets/" + evidence["assets"]["diff"] + "/content"
            page.route(missing, lambda route: route.abort())
            reload = page.get_by_role("button", name="Reload evidence", exact=True)
            # A new document avoids the browser's decoded-image memory cache.
            page.reload()
            expect(
                page.get_by_text("Some evidence images could not be loaded:", exact=False)
            ).to_be_visible()
            decide("consistent", "clear", "Automated fixture: a missing image must block review.")
            expect(record).to_be_disabled()
            page.unroute(missing)
            page.once("dialog", lambda dialog: dialog.accept())
            reload.click()
            expect(page.get_by_label("Review notes", exact=True)).to_have_value("")

            decide("consistent", "clear", "Automated fixture: simulated complete visual approval.")
            expect(record).to_be_enabled()
            record.click()
            expect(verdict).to_have_text("VALID")
            expect(page.get_by_test_id("validation-revision")).to_have_count(2)

            # Another authenticated review changes the report while this tab
            # still has the previous evidence. Never retry that stale decision.
            with stack.factory() as uow:
                service = Service(uow, stack.settings)
                current = service.candidates.dto(service.candidates.row(candidate_id))
                service.candidates.review(
                    candidate_id,
                    MutationReviewRequest(
                        prior_report_sha256=current.validation.report_sha256,
                        evidence_hashes=current.validation.evidence_hashes,
                        semantic="consistent",
                        visibility="clear",
                        notes="Automated fixture: simulated review from another tab.",
                    ),
                )
                uow.commit()
            stale_note = "Automated fixture: simulate a stale semantic rejection."
            decide("additional_object", "clear", stale_note)
            record.click()
            expect(
                page.get_by_text(
                    __import__("re").compile(
                        r"^Review evidence is stale\.(?: Reference: [a-f0-9-]+)?$"
                    )
                )
            ).to_be_visible()
            expect(record).to_be_disabled()
            reload = page.get_by_role("button", name="Reload evidence", exact=True)
            page.once("dialog", lambda dialog: dialog.dismiss())
            reload.click()
            expect(page.get_by_label("Review notes", exact=True)).to_have_value(stale_note)
            page.once("dialog", lambda dialog: dialog.accept())
            reload.click()
            expect(page.get_by_test_id("validation-revision")).to_have_count(3)
            expect(page.get_by_label("Review notes", exact=True)).to_have_value("")

            decide(
                "additional_object", "clear", "Automated fixture: simulate a semantic rejection."
            )
            record.click()
            expect(verdict).to_have_text("INVALID")
            expect(page.get_by_test_id("validation-revision")).to_have_count(4)

            decide(
                "consistent", "clear", "Automated fixture: simulate correcting the prior judgement."
            )
            expect(record).to_be_disabled()
            page.get_by_label("Perform a ground-truth audit", exact=True).check()
            page.get_by_label("Resolve HUMAN_SEMANTICS", exact=True).check()
            page.get_by_label("Ground-truth audit notes", exact=True).fill(
                "Automated fixture: explicit simulated audit resolves the prior semantic finding."
            )
            record.click()
            expect(verdict).to_have_text("VALID")
            expect(page.get_by_test_id("validation-revision")).to_have_count(5)
            expect(page.get_by_text("Ground-truth audit recorded", exact=True)).to_be_visible()
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(stack.tmp_path / "phase04-desktop.png"))
            page.locator(".mutation-review").screenshot(
                path=str(stack.tmp_path / "phase04-review.png")
            )

            decide("consistent", "ambiguous", "Automated fixture: simulate uncertain visibility.")
            record.click()
            expect(verdict).to_have_text("REVIEW_REQUIRED")
            expect(page.get_by_test_id("validation-revision")).to_have_count(6)
            decide(
                "consistent", "destroyed", "Automated fixture: simulate confirmed lost visibility."
            )
            record.click()
            expect(verdict).to_have_text("INVALID")
            expect(page.get_by_test_id("validation-revision")).to_have_count(7)
            page.set_viewport_size({"width": 390, "height": 844})
            page.evaluate("window.scrollTo(0, 0)")
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.screenshot(path=str(stack.tmp_path / "phase04-mobile.png"))

            # Expiry must preserve an unsaved review until the owner renews.
            draft_note = "Automated fixture: retained draft across session renewal."
            decide("additional_object", "destroyed", draft_note)
            context.clear_cookies()
            record.click()
            renewal = page.get_by_role("dialog", name="Renew owner session")
            expect(renewal).to_be_visible()
            renewal.get_by_label("Owner access token").fill(stack.owner)
            renewal.get_by_role("button", name="Open workspace").click()
            expect(renewal).not_to_be_visible()
            expect(page.get_by_label("Review notes", exact=True)).to_have_value(draft_note)
            page.once("dialog", lambda dialog: dialog.accept())
            reload.click()
            expect(page.get_by_label("Review notes", exact=True)).to_have_value("")
            expect(page.get_by_test_id("validation-revision")).to_have_count(7)
            assert stack.owner not in page.content()
            assert not foreign
            page.get_by_role("button", name="Sign out", exact=True).click()
            expect(page.get_by_label("Owner access token")).to_be_visible()
            assert (
                page.request.get(
                    stack.origin + "/api/backend/mutations/" + str(candidate_id)
                ).status
                == 401
            )
            assert (
                page.request.get(
                    stack.origin + "/api/backend/assets/" + str(original_id) + "/content"
                ).status
                == 401
            )
            with stack.factory() as uow:
                from sqlalchemy import func, select

                assert (
                    uow.connection.scalar(
                        select(func.count()).select_from(uow.tables.model_observations)
                    )
                    == 0
                )
        except Exception:
            page.screenshot(path=str(stack.tmp_path / "phase04-failure.png"), full_page=True)
            raise
        finally:
            context.close()
            browser.close()
