"""The base model every API shape uses, and shared aliases."""

from __future__ import annotations

from pydantic import BaseModel as _BaseModel, ConfigDict


Files = dict[str, str]


class BaseModel(_BaseModel):
    # Responses always include fields that have defaults, so the spec (and generated types) mark them required.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
