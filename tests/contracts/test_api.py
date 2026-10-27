import httpx2
import pytest

from services.api.app.main import app


@pytest.fixture
async def client():
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


async def test_health_does_not_claim_unimplemented_dependencies_ready(client):
    assert (await client.get("/api/v1/health/live")).status_code == 200
    assert (await client.get("/api/v1/health/ready")).status_code == 503


async def test_stub_does_not_pretend_to_persist_project(client):
    response = await client.post("/api/v1/projects", json={"name": "Valid project"})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
    assert response.json()["error"]["correlation_id"]


async def test_validation_errors_are_sanitized_project_envelopes(client):
    response = await client.post(
        "/api/v1/projects", json={"name": "x", "secret_ref": "DO_NOT_ECHO_THIS"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_INPUT"
    assert "DO_NOT_ECHO_THIS" not in response.text
    assert "detail" not in response.json()


async def test_bad_uuid_and_unknown_route_use_envelope(client):
    assert (await client.get("/api/v1/projects/not-a-uuid")).json()["error"][
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
        "/api/v1/contracts/00000000-0000-0000-0000-000000000001",
        "/api/v1/jobs/00000000-0000-0000-0000-000000000001",
        "/api/v1/findings/00000000-0000-0000-0000-000000000001",
        "/api/v1/regressions",
    ],
)
async def test_reserved_read_routes_explicitly_remain_unimplemented(client, path):
    response = await client.get(path)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
