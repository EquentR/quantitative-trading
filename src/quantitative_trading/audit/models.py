from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _require_timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class AuditLog(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    audit_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    recommendation_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime, info) -> datetime:
        return _require_timezone_aware(value, info.field_name)
