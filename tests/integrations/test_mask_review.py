from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.integrations.test_contracts_and_jobs import contract, png, setup_case, target


def correction(left=1):
    image = Image.new("L", (12, 8))
    image.paste(255, (left, 1, 10, 7))
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


async def revise(api, seed, mask, *, data=None, key="edit-v1"):
    return await api.client.post(
        f"/api/v1/seeds/{seed['id']}/masks/{mask}/revisions",
        files={"file": ("mask.png", data or correction(), "image/png")},
        data={"notes": "Corrected the exact foreground pixels."},
        headers={"Idempotency-Key": key},
    )


async def test_mask_revision_retains_approval_history_and_never_rewrites_locked_evidence(
    api, vtc_payload
):
    seed, mask, vtc = await contract(api, vtc_payload)
    config = await target(api)
    start_body = {
        "project_id": api.project_id,
        "contract_id": vtc["id"],
        "target_config_id": config["id"],
    }
    run = await api.client.post(
        "/api/v1/experiments", json=start_body, headers={"Idempotency-Key": "historical-run"}
    )
    assert run.status_code == 202
    old = vtc["contract"]["protected"]["mask_asset_ids"][0]
    changed = await revise(api, seed, mask)
    assert changed.status_code == 201, changed.text
    assert changed.json()["revision"] == 2 and not changed.json()["reviewed"]
    assert changed.json()["mask_asset_id"] != old
    history = await api.client.get(f"/api/v1/seeds/{seed['id']}/masks/{mask}/revisions")
    assert history.status_code == 200, history.text
    assert [item["revision"] for item in history.json()["items"]] == [1, 2]
    assert history.json()["items"][0]["reviewed"] is True
    assert history.json()["items"][0]["mask_asset_id"] == old
    assert (await api.client.get(f"/api/v1/assets/{old}/content")).status_code == 200
    assert (await api.client.get(f"/api/v1/contracts/{vtc['id']}")).json()["contract"] == vtc[
        "contract"
    ]
    replay = await api.client.post(
        "/api/v1/experiments", json=start_body, headers={"Idempotency-Key": "historical-run"}
    )
    assert replay.json() == run.json()
    with pytest.raises(DBAPIError), api.factory() as uow:
        uow.connection.execute(text("UPDATE seed_mask_revisions SET notes='rewritten'"))


async def test_same_edit_key_returns_its_original_result_without_resetting_new_approval(
    api, vtc_payload
):
    seed, mask, _ = await setup_case(api, vtc_payload)
    first = await revise(api, seed, mask)
    assert first.status_code == 201, first.text
    approved = await api.client.post(
        f"/api/v1/seeds/{seed['id']}/masks/{mask}/approve",
        json={
            "decision": "VALID",
            "reason_code": "HUMAN_REVIEW",
            "notes": "Reviewed this exact revision.",
        },
    )
    assert approved.json()["reviewed"] is True
    assert (await revise(api, seed, mask)).json() == first.json()
    current = (await api.client.get(f"/api/v1/seeds/{seed['id']}/masks")).json()["items"][0]
    assert current["reviewed"] is True
    conflict = await revise(api, seed, mask, data=correction(2))
    assert (
        conflict.status_code == 409 and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    )


async def test_manual_add_and_retire_are_owner_scoped_and_do_not_change_ground_truth(
    api, vtc_payload
):
    seed, mask, _ = await setup_case(api, vtc_payload)
    added = await api.client.post(
        f"/api/v1/seeds/{seed['id']}/masks",
        files={"file": ("new.png", correction(), "image/png")},
        data={"object_label": "red container", "notes": "Added a distinct manual proposal."},
    )
    assert added.status_code == 201, added.text
    assert not added.json()["reviewed"] and added.json()["origin"] == "HUMAN"
    retired = await api.client.post(
        f"/api/v1/seeds/{seed['id']}/masks/{added.json()['id']}/retire",
        json={
            "decision": "INVALID",
            "reason_code": "EXTRA_INSTANCE",
            "notes": "Removed the extra proposal.",
        },
    )
    assert retired.status_code == 200 and retired.json()["active"] is False
    history = await api.client.get(
        f"/api/v1/seeds/{seed['id']}/masks/{added.json()['id']}/revisions"
    )
    assert history.json()["items"][-1]["retired_at"] is not None
    assert len((await api.client.get(f"/api/v1/seeds/{seed['id']}/masks")).json()["items"]) == 1
    assert (await api.client.get(f"/api/v1/seeds/{seed['id']}")).json()["expected"] == seed[
        "expected"
    ]
    other = await api.client.post("/api/v1/projects", json={"name": "Other mask project"})
    other_seed = await api.client.post(
        f"/api/v1/projects/{other.json()['id']}/seeds",
        files={"file": ("seed.png", png(), "image/png")},
        data={
            "question": "How many red containers?",
            "expected_count": 1,
            "license_ack": True,
            "label_reviewer": "Reviewer",
        },
    )
    assert (await revise(api, other_seed.json(), mask)).status_code == 404


async def test_duplicate_active_mask_bytes_and_transparent_corrections_are_rejected(
    api, vtc_payload
):
    seed, mask, _ = await setup_case(api, vtc_payload)
    duplicate = await api.client.post(
        f"/api/v1/seeds/{seed['id']}/masks",
        files={"file": ("duplicate.png", png("L", "white"), "image/png")},
        data={"object_label": "red container", "notes": "Attempt to reuse exact same instance."},
    )
    assert duplicate.status_code == 422, duplicate.text
    transparent = BytesIO()
    Image.new("RGBA", (12, 8), (255, 255, 255, 0)).save(transparent, "PNG")
    assert (await revise(api, seed, mask, data=transparent.getvalue())).status_code == 422


async def test_four_detections_for_five_require_correction_review_and_fresh_acknowledgement(api):
    from uuid import UUID

    from services.api.app.service import Service

    uploaded = await api.client.post(
        f"/api/v1/projects/{api.project_id}/seeds",
        files={"file": ("seed.png", png(), "image/png")},
        data={
            "question": "How many red containers?",
            "expected_count": 5,
            "license_ack": True,
            "label_reviewer": "Human reviewer",
        },
    )
    seed = uploaded.json()
    seed_id = UUID(seed["id"])
    queued = await api.client.post(
        f"/api/v1/seeds/{seed_id}/segment", json={"concept": "red container"}
    )
    assert queued.status_code == 202, queued.text
    with api.factory() as uow:
        service = Service(uow, api.settings)
        proposals = []
        for left in range(4):
            binary = Image.new("L", (12, 8))
            binary.paste(255, (left * 2, 1, left * 2 + 1, 7))
            output = BytesIO()
            binary.save(output, "PNG")
            mask = service.mask_review.write(
                seed_id, output.getvalue(), notes="Draft fixture mask", object_label="red container"
            )
            proposals.append((mask.mask_asset_id, mask.geometry.model_dump()))
        generation = service.seed(seed_id)["mask_generation"]
        assert service.mask_review.apply_proposals(
            seed_id,
            job_id=UUID(queued.json()["job_id"]),
            expected_generation=generation,
            proposals=proposals,
            object_label="red container",
            outcome="COMPLETED",
            diagnostics=[],
            synthetic=True,
        )
        uow.commit()
    path = f"/api/v1/seeds/{seed_id}"
    state = (await api.client.get(path + "/mask-review")).json()
    assert (state["detected_count"], state["expected_count"], state["draft_count"]) == (4, 5, 4)
    assert not state["can_create_contract"]
    fifth = Image.new("L", (12, 8))
    fifth.paste(255, (9, 1, 10, 7))
    output = BytesIO()
    fifth.save(output, "PNG")
    added = await api.client.post(
        path + "/masks",
        files={"file": ("fifth.png", output.getvalue(), "image/png")},
        data={"notes": "Added the missing fifth distinct object.", "object_label": "red container"},
    )
    assert added.status_code == 201, added.text
    masks = (await api.client.get(path + "/masks")).json()["items"]
    for mask in masks:
        assert (
            await api.client.post(
                path + f"/masks/{mask['id']}/approve",
                json={
                    "decision": "VALID",
                    "reason_code": "HUMAN_REVIEW",
                    "notes": "Reviewed this distinct object.",
                },
            )
        ).status_code == 200
    state = (await api.client.get(path + "/mask-review")).json()
    assert state["approved_count"] == 5 and not state["can_create_contract"]
    ack = await api.client.post(
        path + "/masks/acknowledgements",
        json={
            "detected_count": 4,
            "human_count": 5,
            "notes": "Corrected the missed fifth object and reviewed every mask.",
        },
    )
    assert ack.status_code == 201 and ack.json()["can_create_contract"], ack.text
    with api.factory() as uow:
        service = Service(uow, api.settings)
        assert not service.mask_review.apply_proposals(
            seed_id,
            job_id=UUID(queued.json()["job_id"]),
            expected_generation=generation,
            proposals=[],
            object_label="red container",
            outcome="INCOMPLETE",
            diagnostics=["LATE"],
        )
        uow.commit()
    assert (await api.client.get(path + "/mask-review")).json() == ack.json()
    assert (await revise(api, seed, masks[0]["id"], key="after-ack")).status_code == 201
    state = (await api.client.get(path + "/mask-review")).json()
    assert not state["acknowledged"] and not state["can_create_contract"]
    assert (await api.client.get(path)).json()["expected"]["value"] == 5


async def test_identical_mask_revision_cannot_clear_existing_approval(api, vtc_payload):
    seed, mask, _ = await setup_case(api, vtc_payload)
    response = await revise(api, seed, mask, data=png("L", "white"), key="no-pixel-change")
    assert response.status_code == 422
    history = (await api.client.get(f"/api/v1/seeds/{seed['id']}/masks/{mask}/revisions")).json()[
        "items"
    ]
    assert len(history) == 1 and history[0]["reviewed"] and history[0]["revision"] == 1
