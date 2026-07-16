from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantitative_trading.instrument.models import InstrumentMetadata


class UniverseSource(StrEnum):
    HOLDING = "holding"
    WATCH_PINNED = "watch_pinned"


class UniverseSnapshotStatus(StrEnum):
    OK = "ok"


def _require_timezone_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class UniverseMember(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    instrument: InstrumentMetadata | None = None
    sources: list[UniverseSource] = Field(min_length=1)
    priority: int = Field(ge=0)
    ledger_updated_at: datetime | None = None
    watch_pinned_rank: int | None = Field(default=None, ge=1)
    plan_enabled: bool
    plan_enabled_source: UniverseSource
    created_at: datetime

    @field_validator("ledger_updated_at", "created_at")
    @classmethod
    def datetimes_must_be_timezone_aware(
        cls,
        value: datetime | None,
        info: Any,
    ) -> datetime | None:
        return _require_timezone_aware(value, info.field_name)

    @model_validator(mode="after")
    def instrument_symbol_must_match(self) -> "UniverseMember":
        if self.instrument is not None and self.instrument.symbol != self.symbol:
            raise ValueError("symbol must match instrument symbol")
        return self


class UniverseSnapshot(BaseModel):
    created_at: datetime
    status: UniverseSnapshotStatus
    warnings: list[str]
    members: list[UniverseMember]

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value, "created_at")
