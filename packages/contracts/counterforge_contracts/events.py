from typing import Literal
from uuid import UUID

from pydantic import Field

from .base import ContractModel
from .versions import ExperimentState, ValidityVerdict


class StateChanged(ContractModel):
    event: Literal["state_changed"] = "state_changed"
    experiment_id: UUID
    state: ExperimentState


class CandidateValidated(ContractModel):
    event: Literal["candidate_validated"] = "candidate_validated"
    experiment_id: UUID
    mutation_id: UUID
    verdict: ValidityVerdict


class ExperimentEvent(ContractModel):
    id: int = Field(ge=1)
    data: StateChanged | CandidateValidated = Field(discriminator="event")
