from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class QuoteStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    STALE = "stale"


def _must_be_timezone_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


class QuoteSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = ""
    current_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    change_pct: float | None = Field(default=None, allow_inf_nan=False)
    data_time: datetime | None = None
    fetched_at: datetime
    source: str = Field(min_length=1)
    status: QuoteStatus
    warning: str = ""

    @field_validator("data_time", "fetched_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)

    @model_validator(mode="after")
    def status_fields_must_match_quote_contract(self) -> "QuoteSnapshot":
        if self.status is QuoteStatus.OK:
            if self.current_price is None:
                raise ValueError("ok quote requires current_price")
            if self.data_time is None:
                raise ValueError("ok quote requires data_time")
        elif self.status is QuoteStatus.PARTIAL:
            if self.current_price is None:
                raise ValueError("partial quote requires current_price")
            if self.data_time is None:
                raise ValueError("partial quote requires data_time")
            if not self.warning:
                raise ValueError("partial quote requires warning")
        elif self.status is QuoteStatus.STALE:
            if self.current_price is None:
                raise ValueError("stale quote requires current_price")
            if self.data_time is None:
                raise ValueError("stale quote requires data_time")
            if not self.warning:
                raise ValueError("stale quote requires warning")
        elif self.status is QuoteStatus.FAILED and not self.warning:
            raise ValueError("failed quote requires warning")
        return self


class MarketInputSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_snapshot_id: int = Field(gt=0)
    quote_snapshot_refs: dict[str, int]
    history_snapshot_refs: dict[str, int]
    money_flow_snapshot_refs: dict[str, int]
    intraday_strength_snapshot_refs: dict[str, int]
    data_time: datetime | None = None
    fetched_at: datetime
    warnings: list[str]

    @field_validator("data_time", "fetched_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        return _must_be_timezone_aware(value)

    @field_validator(
        "quote_snapshot_refs",
        "history_snapshot_refs",
        "money_flow_snapshot_refs",
        "intraday_strength_snapshot_refs",
    )
    @classmethod
    def references_must_be_valid(cls, value: dict[str, int]) -> dict[str, int]:
        if any(len(symbol) != 6 or not symbol.isdigit() for symbol in value):
            raise ValueError("snapshot reference symbols must contain six digits")
        if any(reference_id <= 0 for reference_id in value.values()):
            raise ValueError("snapshot reference ids must be positive")
        return value
