from uuid import UUID


async def advance_experiment(experiment_id: UUID) -> None:
    from services.api.app.config import Settings
    from services.api.app.db import create_database_engine, create_uow_factory

    from .experiments import execute_experiment

    settings = Settings.from_env()
    engine = create_database_engine(settings)
    try:
        await execute_experiment(create_uow_factory(engine), settings, experiment_id)
    finally:
        engine.dispose()


def run_setup_job(job_id: str) -> None:
    """The sole RQ entry point accepts a canonical UUID string, never pickled inputs."""
    import asyncio

    from services.api.app.config import Settings
    from services.api.app.db import create_database_engine, create_uow_factory

    from .jobs import execute_job

    parsed = UUID(job_id)
    if str(parsed) != job_id:
        raise ValueError("Job payload must be a canonical UUID")
    settings = Settings.from_env()
    engine = create_database_engine(settings)
    try:
        asyncio.run(execute_job(create_uow_factory(engine), settings, parsed))
    finally:
        engine.dispose()


def run_experiment(experiment_id: str) -> None:
    import asyncio

    parsed = UUID(experiment_id)
    if str(parsed) != experiment_id:
        raise ValueError("Job payload must be a canonical UUID")
    asyncio.run(advance_experiment(parsed))
