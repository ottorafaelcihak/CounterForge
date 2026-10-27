from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

FiniteNumber = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_default=True)
