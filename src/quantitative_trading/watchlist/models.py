from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WatchPinnedSource(StrEnum):
    MANUAL = "manual"
    SYNCED = "synced"
    MANUAL_SYNCED = "manual_synced"


class WatchPinnedInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    rank: int = Field(ge=1)
    plan_enabled: bool = False
    note: str = ""


class WatchPinnedItem(WatchPinnedInput):
    source: WatchPinnedSource
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_timezone_aware(cls, value: datetime, info: Any) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value
