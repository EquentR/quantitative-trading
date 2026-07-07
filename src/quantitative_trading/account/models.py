from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PositionValuationStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    STALE = "stale"


class AccountSnapshotStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    MARKET_DATA_UNAVAILABLE = "market_data_unavailable"
    CASH_NOT_INITIALIZED = "cash_not_initialized"


def _require_timezone_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


class PositionValuation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    available_quantity: int = Field(ge=0)
    cost_price: float = Field(gt=0, allow_inf_nan=False)
    position_cost: float = Field(ge=0, allow_inf_nan=False)
    current_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    market_value: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    floating_pnl: float | None = Field(default=None, allow_inf_nan=False)
    floating_pnl_pct: float | None = Field(default=None, allow_inf_nan=False)
    ledger_updated_at: datetime
    quote_data_time: datetime | None = None
    quote_fetched_at: datetime | None = None
    status: PositionValuationStatus
    warning: str = ""

    @field_validator("ledger_updated_at", "quote_data_time", "quote_fetched_at")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime | None, info: Any) -> datetime | None:
        return _require_timezone_aware(value, info.field_name)


class AccountSnapshot(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    cash_balance: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    net_principal: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    market_value: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    position_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    floating_pnl: float | None = Field(default=None, allow_inf_nan=False)
    floating_pnl_pct: float | None = Field(default=None, allow_inf_nan=False)
    total_assets: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    total_pnl: float | None = Field(default=None, allow_inf_nan=False)
    total_pnl_pct: float | None = Field(default=None, allow_inf_nan=False)
    position_ratio: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    available_buying_cash: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    positions: list[PositionValuation]
    status: AccountSnapshotStatus
    warnings: list[str]
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _require_timezone_aware(value, "created_at")
