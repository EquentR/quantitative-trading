from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentType,
    SettlementCycle,
)


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
    exchange: Exchange | None = None
    instrument_type: InstrumentType = InstrumentType.UNKNOWN
    settlement_cycle: SettlementCycle = SettlementCycle.UNKNOWN
    price_limit_ratio: float | None = None
    metadata_source: str = "legacy_unverified"
    metadata_checked_at: datetime | None = None
    rule_version: str = "unverified-v1"
    warnings: list[str] = Field(default_factory=list)

    @field_validator("updated_at", "metadata_checked_at")
    @classmethod
    def timestamps_must_be_timezone_aware(
        cls,
        value: datetime | None,
        info: Any,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value


class WatchPinnedImportResult(BaseModel):
    items: list[WatchPinnedItem]
    warnings: list[str]
