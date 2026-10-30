"""Stored owner DTOs reconcile across dashboard, cockpit and rejection explorer."""

from uuid import UUID

from sqlalchemy import select

from tests.integrations.test_candidates import create, experiment, review_body


async def test_metrics_preserve_rejected_attempts_reports_and_unknown_denominators(
    api, vtc_payload
):
    eid, _, _, _ = await experiment(api, vtc_payload)
    mutation = (await create(api, eid)).json()
    reviewed = await api.client.post(
        f"/api/v1/mutations/{mutation['id']}/review",
        json=review_body(mutation, semantic="additional_object", notes="Simulated rejected edit."),
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["validation"]["verdict"] == "INVALID"
    with api.factory() as uow:
        from services.api.app.service import Service

        target_id = Service(uow, api.settings).experiment(UUID(eid))["target_config_id"]
        observations = uow.tables.model_observations
        uow.connection.execute(
            observations.insert().values(
                experiment_id=UUID(eid),
                mutation_id=UUID(mutation["id"]),
                phase="SCREENING",
                attempt_no=1,
                outcome="UNKNOWN",
                target_config_id=target_id,
                request_fingerprint="0" * 64,
                parsed_json={"outcome": "UNKNOWN"},
            )
        )
        uow.commit()
    metrics = (await api.client.get(f"/api/v1/experiments/{eid}/metrics")).json()
    assert metrics["generated_candidates"] == 1 and metrics["invalid_candidates"] == 1
    assert metrics["screening"] == {
        "total": 1,
        "interpretable": 0,
        "correct": 0,
        "incorrect": 0,
        "unknown": 1,
        "provider_errors": 0,
    }
    assert metrics["confirmation"]["total"] == 0
    assert metrics["stable_findings"] == 0 and metrics["finding_counts"] == {}
    report = metrics["report_references"][0]
    assert report["mutation_id"] == mutation["id"] and report["revision"] == 2
    assert report["report_asset_id"] == reviewed.json()["assets"]["report"]
    listing = (await api.client.get(f"/api/v1/projects/{api.project_id}/experiments")).json()
    assert listing["items"][0]["metrics"] == metrics
    assert (await api.client.get(f"/api/v1/experiments/{eid}")).json()["metrics"] == metrics
    gallery = (await api.client.get(f"/api/v1/experiments/{eid}/mutations")).json()
    assert gallery["items"][0]["validation"]["verdict"] == "INVALID"
    with api.factory() as uow:
        assert list(uow.connection.scalars(select(uow.tables.usage_events.c.id))) == []
