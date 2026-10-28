import hashlib
import io
from uuid import UUID

import pytest
from PIL import Image
from sqlalchemy import select


def upload_bytes():
    output = io.BytesIO()
    exif = Image.Exif()
    exif[270] = "private source metadata"
    Image.new("RGB", (12, 8), "red").save(output, format="JPEG", exif=exif)
    return output.getvalue()


async def upload(api, **overrides):
    fields = {
        "question": "How many containers are visible?",
        "expected_count": "1",
        "license_ack": "true",
        "label_reviewer": "Human reviewer",
    }
    fields.update(overrides)
    return await api.client.post(
        f"/api/v1/projects/{api.project_id}/seeds",
        data=fields,
        files={"file": ("../../ignored.jpg", upload_bytes(), "image/jpeg")},
    )


async def test_seed_round_trip_keeps_original_evidence_and_normalized_asset(api):
    response = await upload(api)
    assert response.status_code == 201, response.text
    seed = response.json()
    provenance = seed["provenance"]
    assert seed["sha256"] == hashlib.sha256(upload_bytes()).hexdigest()
    assert provenance["original_sha256"] == seed["sha256"]
    assert provenance["normalized_asset_id"] == seed["image_asset_id"]
    assert provenance["original_asset_id"] != seed["image_asset_id"]
    assert provenance["rights_acknowledged"] is True
    assert seed["expected"]["reviewer"] == "Human reviewer"
    original = await api.client.get(f"/api/v1/assets/{provenance['original_asset_id']}/content")
    assert original.content == upload_bytes()
    normalized = await api.client.get(f"/api/v1/assets/{seed['image_asset_id']}/content")
    assert normalized.headers["content-type"].startswith("image/png")
    assert hashlib.sha256(normalized.content).hexdigest() == provenance["normalized_sha256"]
    assert (await api.client.get(f"/api/v1/seeds/{seed['id']}")).json() == seed
    page = (await api.client.get(f"/api/v1/projects/{api.project_id}/seeds")).json()
    assert page == {"items": [seed], "next_cursor": None}
    assert "ignored" not in str(list(api.root.rglob("*")))
    with api.factory() as uow:
        events = uow.connection.execute(select(uow.tables.audit_events)).mappings().all()
        assert {row["action"] for row in events} >= {"PROJECT_CREATED", "SEED_CREATED"}
        assert "phase02-test-owner-token" not in str(events)
        assert "private source metadata" not in str(events)


@pytest.mark.parametrize("fields", [{"license_ack": "false"}, {"label_reviewer": " "}])
async def test_upload_requires_rights_and_named_human_label_reviewer(api, fields):
    response = await upload(api, **fields)
    assert response.status_code == 422
    with api.factory() as uow:
        assert uow.connection.execute(select(uow.tables.seed_cases)).all() == []
        assert uow.connection.execute(select(uow.tables.artifact_assets)).all() == []


async def test_upload_rejects_mime_mismatch_without_registering_files(api):
    response = await api.client.post(
        f"/api/v1/projects/{api.project_id}/seeds",
        data={
            "question": "How many containers?",
            "expected_count": 1,
            "license_ack": True,
            "label_reviewer": "Reviewer",
        },
        files={"file": ("image.png", upload_bytes(), "image/png")},
    )
    assert response.status_code == 422
    assert not list(api.root.rglob("*.png"))
    with api.factory() as uow:
        assert uow.connection.execute(select(uow.tables.artifact_assets)).all() == []


async def test_private_content_requires_owner_and_is_verified_before_serving(api):
    seed = (await upload(api)).json()
    path = f"/api/v1/assets/{seed['image_asset_id']}/content"
    for token in ("", "Bearer wrong", "Basic phase02-test-owner-token"):
        response = await api.client.get(path, headers={"Authorization": token})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"
    with api.factory() as uow:
        assets = uow.tables.artifact_assets
        key = uow.connection.scalar(
            select(assets.c.storage_key).where(assets.c.id == UUID(seed["image_asset_id"]))
        )
    (api.root / key).write_bytes(b"tampered")
    response = await api.client.get(path)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "ARTIFACT_CHECKSUM_MISMATCH"
    assert "tampered" not in response.text and str(api.root) not in response.text


async def test_owner_is_scoped_and_cursor_does_not_cross_resource_lists(api):
    with api.factory() as uow:
        foreign = uow.connection.scalar(
            uow.tables.projects.insert()
            .values(name="Foreign project", owner_label="another-owner")
            .returning(uow.tables.projects.c.id)
        )
        uow.commit()
    assert (await api.client.get(f"/api/v1/projects/{foreign}")).status_code == 403
    await api.client.post("/api/v1/projects", json={"name": "Second project"})
    first = await api.client.get("/api/v1/projects?limit=1")
    cursor = first.json()["next_cursor"]
    assert cursor
    second = await api.client.get("/api/v1/projects", params={"limit": 1, "cursor": cursor})
    assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]
    assert second.json()["next_cursor"] is None
    replay = await api.client.get(
        f"/api/v1/projects/{api.project_id}/seeds", params={"cursor": cursor}
    )
    assert replay.status_code == 422
    assert (await api.client.get("/api/v1/projects?limit=101")).status_code == 422
    assert (await api.client.get("/api/v1/projects?cursor=garbage")).status_code == 422


async def test_cors_preflight_is_public_and_restricted_to_web_origin(api):
    headers = {"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}
    good = await api.client.options("/api/v1/projects", headers=headers)
    assert good.status_code == 200
    assert good.headers["access-control-allow-origin"] == "http://localhost:3000"
    bad = await api.client.options(
        "/api/v1/projects", headers={**headers, "Origin": "https://evil.invalid"}
    )
    assert bad.status_code == 400 and "access-control-allow-origin" not in bad.headers


async def test_streamed_oversized_upload_is_rejected_before_multipart_spooling(api):
    async def body():
        for _ in range(12):
            yield b"x" * 1024 * 1024

    response = await api.client.post(
        f"/api/v1/projects/{api.project_id}/seeds",
        content=body(),
        headers={"Content-Type": "multipart/form-data; boundary=missing"},
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "INVALID_INPUT"
    assert list(api.root.iterdir()) == []
