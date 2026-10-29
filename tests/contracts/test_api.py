import httpx2
import pytest
from pydantic import SecretStr

from services.api.app.config import Settings
from services.api.app.main import create_app


@pytest.fixture
async def client():
    app = create_app(
        settings=Settings(
            owner_auth_token=SecretStr("contract-test-owner"),
            redis_url=SecretStr("redis://127.0.0.1:1/0"),
        )
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer contract-test-owner"},
    ) as value:
        yield value


@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_health_does_not_claim_unimplemented_dependencies_ready(client):
    assert (await client.get("/api/v1/health/live")).status_code == 200
    assert (await client.get("/api/v1/health/ready")).status_code == 503


async def test_project_creation_requires_owner_before_persistence(client):
    response = await client.post(
        "/api/v1/projects", json={"name": "Valid project"}, headers={"Authorization": ""}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert response.json()["error"]["correlation_id"]


async def test_validation_errors_are_sanitized_project_envelopes(client):
    response = await client.post(
        "/api/v1/mutations/00000000-0000-0000-0000-000000000001/review",
        json={
            "decision": "INVALID",
            "reason_code": "",
            "notes": "x",
            "secret_ref": "DO_NOT_ECHO_THIS",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert "DO_NOT_ECHO_THIS" not in response.text
    assert "detail" not in response.json()


async def test_bad_uuid_and_unknown_route_use_envelope(client):
    assert (await client.post("/api/v1/experiments/not-a-uuid/cancel")).json()["error"][
        "code"
    ] == "INVALID_INPUT"
    assert (await client.get("/missing")).json()["error"]["code"] == "NOT_FOUND"


async def test_openapi_registers_real_typed_contracts(client):
    schema = (await client.get("/openapi.json")).json()
    paths = schema["paths"]
    assert "/api/v1/experiments" in paths
    assert "/api/v1/regressions/{export_id}/retest" in paths
    assert "/api/v1/public/demo" in paths
    assert "201" in paths["/api/v1/projects"]["post"]["responses"]
    assert "202" in paths["/api/v1/experiments"]["post"]["responses"]
    response = paths["/api/v1/projects"]["post"]["responses"]["201"]
    assert response["content"]["application/json"]["schema"]["$ref"].endswith("/Project")


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects",
        "/api/v1/projects/00000000-0000-0000-0000-000000000001/seeds",
        "/api/v1/projects/00000000-0000-0000-0000-000000000001/targets",
        "/api/v1/seeds/00000000-0000-0000-0000-000000000001",
        "/api/v1/seeds/00000000-0000-0000-0000-000000000001/masks",
        "/api/v1/seeds/00000000-0000-0000-0000-000000000001/contracts",
        "/api/v1/contracts/00000000-0000-0000-0000-000000000001",
        "/api/v1/jobs/00000000-0000-0000-0000-000000000001",
        "/api/v1/findings/00000000-0000-0000-0000-000000000001",
        "/api/v1/regressions",
    ],
)
async def test_private_read_routes_require_owner(client, path):
    response = await client.get(path, headers={"Authorization": ""})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_scheduler_controls_require_persistence_and_owner(client):
    response = await client.post("/api/v1/experiments/00000000-0000-0000-0000-000000000001/cancel")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
    denied = await client.post(
        "/api/v1/experiments/00000000-0000-0000-0000-000000000001/cancel",
        headers={"Authorization": ""},
    )
    assert denied.status_code == 401
