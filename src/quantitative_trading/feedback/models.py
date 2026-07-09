from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _require_timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class ExecutionFeedbackInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    recommendation_id: str = Field(min_length=1)
    executed: bool
    execution_price: float | None = Field(default=None, gt=0)
    execution_quantity: int | None = Field(default=None, gt=0)
    note: str = ""


class ExecutionFeedback(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    feedback_id: str = Field(min_length=1)
    recommendation_id: str = Field(min_length=1)
    executed: bool
    execution_price: float | None = Field(default=None, gt=0)
    execution_quantity: int | None = Field(default=None, gt=0)
    note: str = ""
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime, info) -> datetime:
        return _require_timezone_aware(value, info.field_name)
