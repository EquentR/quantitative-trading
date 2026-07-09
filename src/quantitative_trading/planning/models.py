from __future__ import annotations

import math
import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradingPlanStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    STALE = "stale"


SYMBOL_PATTERN = re.compile(r"^\d{6}$")


def _require_timezone_aware(value: datetime | None, field_name: str) -> datetime | None:
    if value is None:
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _require_symbol(value: str) -> str:
    if not SYMBOL_PATTERN.fullmatch(value):
        raise ValueError("symbol must be a six-digit A-share symbol")
    return value


class TradingPlan(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    plan_id: str = Field(min_length=1)
    trading_day: date
    generated_at: datetime
    valid_until: datetime
    universe_snapshot_id: int = Field(gt=0)
    account_snapshot_id: int | None = Field(default=None, gt=0)
    ledger_max_updated_at: datetime | None = None
    watch_symbols: list[str]
    holding_symbols: list[str]
    key_levels: dict[str, dict[str, float]]
    candidate_actions: dict[str, list[str]]
    invalid_if: dict[str, list[str]]
    warnings: list[str]
    status: TradingPlanStatus

    @field_validator("generated_at", "valid_until", "ledger_max_updated_at")
    @classmethod
    def datetimes_must_be_timezone_aware(
        cls,
        value: datetime | None,
        info: Any,
    ) -> datetime | None:
        return _require_timezone_aware(value, info.field_name)

    @field_validator("watch_symbols", "holding_symbols")
    @classmethod
    def symbol_lists_must_use_a_share_codes(cls, value: list[str]) -> list[str]:
        for symbol in value:
            _require_symbol(symbol)
        return value

    @field_validator("key_levels")
    @classmethod
    def key_levels_must_use_a_share_codes(
        cls,
        value: dict[str, dict[str, float]],
    ) -> dict[str, dict[str, float]]:
        for symbol, levels in value.items():
            _require_symbol(symbol)
            for level_name, level_value in levels.items():
                if not math.isfinite(level_value):
                    raise ValueError(f"key level {symbol}.{level_name} must be finite")
        return value

    @field_validator("candidate_actions", "invalid_if")
    @classmethod
    def symbol_maps_must_use_a_share_codes(
        cls,
        value: dict[str, list[str]],
    ) -> dict[str, list[str]]:
        for symbol in value:
            _require_symbol(symbol)
        return value
