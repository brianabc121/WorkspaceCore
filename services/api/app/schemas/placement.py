"""One placement contract for managed agent and runner server pools."""

from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator


class Placement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "specified"] = "auto"
    server_id: UUID | None = None

    @model_validator(mode="after")
    def target_matches_mode(self) -> Self:
        if (self.mode == "specified") != (self.server_id is not None):
            raise ValueError("specified requires server_id; auto must omit server_id")
        return self
