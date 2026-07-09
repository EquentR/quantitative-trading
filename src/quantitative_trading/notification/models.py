from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _require_timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class NotificationStatus(StrEnum):
    UNREAD = "unread"
    READ = "read"
    FEEDBACK_RECORDED = "feedback_recorded"


class NotificationSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    notification_id: str = Field(min_length=1)
    recommendation_id: str = Field(min_length=1)
    symbol: str = Field(pattern=r"^\d{6}$")
    action: str = Field(min_length=1)
    confidence: str = Field(min_length=1)
    key_price: float | None
    reason: list[str] = Field(min_length=1)
    risk: list[str]
    data_time: datetime
    audit_id: str = Field(min_length=1)
    status: NotificationStatus = NotificationStatus.UNREAD
    created_at: datetime

    @field_validator("data_time", "created_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime, info) -> datetime:
        return _require_timezone_aware(value, info.field_name)
