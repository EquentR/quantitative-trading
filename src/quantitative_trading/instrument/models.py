from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Exchange(StrEnum):
    SH = "SH"
    SZ = "SZ"


class InstrumentType(StrEnum):
    A_SHARE = "a_share"
    ETF = "etf"
    UNKNOWN = "unknown"


class SettlementCycle(StrEnum):
    T0 = "t0"
    T1 = "t1"
    UNKNOWN = "unknown"


class InstrumentPreviewSource(StrEnum):
    EASTMONEY_WATCHLIST = "eastmoney_watchlist"
    INSTRUMENT_SEARCH = "instrument_search"


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _text_list(values: list[str], field_name: str) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError(f"{field_name} cannot contain blank text")
    return cleaned


class InstrumentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^[0-9]{6}$")
    name: str = Field(min_length=1)
    exchange: Exchange | None
    instrument_type: InstrumentType
    settlement_cycle: SettlementCycle
    price_limit_ratio: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    metadata_source: str = Field(min_length=1)
    metadata_checked_at: datetime
    rule_version: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("metadata_checked_at")
    @classmethod
    def checked_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _aware(value, "metadata_checked_at")

    @field_validator("warnings")
    @classmethod
    def warnings_must_be_readable(cls, value: list[str]) -> list[str]:
        return _text_list(value, "warnings")

    @model_validator(mode="after")
    def instrument_rules_must_be_consistent(self) -> "InstrumentMetadata":
        if self.instrument_type is not InstrumentType.UNKNOWN and self.exchange is None:
            raise ValueError("known instrument requires exchange")
        if (
            self.instrument_type is InstrumentType.A_SHARE
            and self.settlement_cycle is not SettlementCycle.T1
        ):
            raise ValueError("A-share settlement must be t1")
        if (
            self.instrument_type is InstrumentType.UNKNOWN
            and self.settlement_cycle is not SettlementCycle.UNKNOWN
        ):
            raise ValueError("unknown instrument requires unknown settlement")
        return self


class InstrumentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^[0-9]{6}$")
    name: str = Field(min_length=1)
    exchange: Exchange | None
    instrument_type: InstrumentType
    settlement_cycle: SettlementCycle
    price_limit_ratio: float | None = Field(default=None, gt=0, le=1, allow_inf_nan=False)
    metadata_source: str = Field(min_length=1)
    metadata_checked_at: datetime
    rule_version: str = Field(min_length=1)
    source: InstrumentPreviewSource
    source_rank: int | None = Field(default=None, ge=1)
    already_monitored: bool
    selectable: bool
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def from_metadata(
        cls,
        metadata: InstrumentMetadata,
        *,
        source: InstrumentPreviewSource,
        source_rank: int | None,
        already_monitored: bool,
        selectable: bool,
        warnings: list[str] | None = None,
    ) -> "InstrumentCandidate":
        return cls(
            **metadata.model_dump(exclude={"warnings"}),
            source=source,
            source_rank=source_rank,
            already_monitored=already_monitored,
            selectable=selectable,
            warnings=list(metadata.warnings if warnings is None else warnings),
        )

    def to_metadata(self) -> InstrumentMetadata:
        return InstrumentMetadata(
            symbol=self.symbol,
            name=self.name,
            exchange=self.exchange,
            instrument_type=self.instrument_type,
            settlement_cycle=self.settlement_cycle,
            price_limit_ratio=self.price_limit_ratio,
            metadata_source=self.metadata_source,
            metadata_checked_at=self.metadata_checked_at,
            rule_version=self.rule_version,
            warnings=list(self.warnings),
        )

    @field_validator("metadata_checked_at")
    @classmethod
    def checked_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _aware(value, "metadata_checked_at")

    @field_validator("warnings")
    @classmethod
    def warnings_must_be_readable(cls, value: list[str]) -> list[str]:
        return _text_list(value, "warnings")


class InstrumentPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    preview_id: UUID
    source: InstrumentPreviewSource
    query: str | None = Field(default=None, max_length=40)
    created_at: datetime
    expires_at: datetime
    items: list[InstrumentCandidate]
    warnings: list[str] = Field(default_factory=list)

    @field_validator("created_at", "expires_at")
    @classmethod
    def times_must_be_timezone_aware(cls, value: datetime, info: Any) -> datetime:
        return _aware(value, info.field_name)

    @field_validator("warnings")
    @classmethod
    def warnings_must_be_readable(cls, value: list[str]) -> list[str]:
        return _text_list(value, "warnings")

    @model_validator(mode="after")
    def expiry_must_follow_creation(self) -> "InstrumentPreview":
        if self.expires_at <= self.created_at:
            raise ValueError("preview expiry must follow creation")
        symbols = [item.symbol for item in self.items]
        if len(symbols) != len(set(symbols)):
            raise ValueError("preview candidates must use unique symbols")
        return self
