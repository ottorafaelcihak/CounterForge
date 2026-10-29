"""Unsealed historical images survive migration without becoming canonical evidence."""

from uuid import UUID

from counterforge_contracts.plans import MutationPlan
from counterforge_mutations.registry import approve_plan, generate_mutation
from sqlalchemy import text

from services.api.app.db.migrations import upgrade_database
from services.api.app.service import Service
from tests.integrations.test_candidates import PLAN, create, experiment
from tests.integrations.test_migrations import evidence


def test_legacy_duplicate_images_survive_upgrade(database_engine):
    upgrade_database(database_engine, "0003_phase03_support")
    with database_engine.begin() as connection:
        graph = evidence(connection)
        asset = connection.scalar(
            text(
                "INSERT INTO artifact_assets (project_id,kind,storage_key,sha256,byte_size,media_type) VALUES (:p,'MUTATION_IMAGE','legacy-candidate.png',:sha,10,'image/png') RETURNING id"
            ),
            {"p": graph["project"], "sha": "a" * 64},
        )
        connection.execute(
            text("UPDATE mutations SET image_asset_id=:a WHERE id=:m"),
            {"a": asset, "m": graph["mutation"]},
        )
        connection.execute(
            text(
                "INSERT INTO mutations (experiment_id,image_asset_id,family,strength,generator,recipe_json,state) VALUES (:e,:a,'background_replacement',0.2,'DETERMINISTIC','{}','GENERATED')"
            ),
            {"e": graph["experiment"], "a": asset},
        )
    upgrade_database(database_engine)
    with database_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM mutations")) == 2


async def test_legacy_image_does_not_hide_new_sealed_candidate(api, vtc_payload):
    eid, *_ = await experiment(api, vtc_payload)
    with api.factory() as uow:
        service = Service(uow, api.settings)
        row = service.experiment(UUID(eid))
        contract, source, masks = service.candidates.snapshot(
            row["contract_id"], row["config_snapshot"], row["project_id"]
        )
        generated = generate_mutation(
            source, masks, approve_plan(contract, MutationPlan.model_validate(PLAN)), 42
        )
        asset = service.artifacts.put(
            row["project_id"],
            generated.png,
            "MUTATION_IMAGE",
            "image/png",
            width=generated.recipe.width,
            height=generated.recipe.height,
        )
        legacy_id = uow.connection.scalar(
            uow.tables.mutations.insert()
            .values(
                experiment_id=UUID(eid),
                image_asset_id=asset.id,
                family="background_replacement",
                strength=0.1,
                generator="DETERMINISTIC",
                recipe_json={},
                state="GENERATED",
            )
            .returning(uow.tables.mutations.c.id)
        )
        uow.commit()
    response = await create(api, eid)
    assert response.status_code == 201, response.text
    candidate = response.json()
    assert candidate["id"] != str(legacy_id)
    assert candidate["validation"]["report_sha256"]
    assert candidate["validation"]["verdict"] == "REVIEW_REQUIRED"
