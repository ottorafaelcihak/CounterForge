from uuid import UUID


async def advance_experiment(experiment_id: UUID) -> None:
    """Phase 01 boundary only; durable scheduling is implemented in phase 05."""
    raise NotImplementedError("Experiment scheduling is not implemented in Phase 01")
