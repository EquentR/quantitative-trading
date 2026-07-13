from __future__ import annotations

import math
import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TradingPlanStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    EXPIRED = "expired"
    STALE = "stale"


SYMBOL_PATTERN = re.compile(r"^\d{6}$")
PlanAction = Literal["buy", "sell", "add", "reduce", "hold", "watch", "avoid"]
PlanDataQuality = Literal["complete", "degraded", "failed", "stale"]


class PlanCondition(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    condition_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    operator: Literal["gt", "gte", "lt", "lte", "eq"]
    threshold: float | str
    required: bool = True
    rationale: str = Field(min_length=1)

    @field_validator("threshold")
    @classmethod
    def threshold_must_be_finite(cls, value: float | str) -> float | str:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("condition threshold must be finite")
        if isinstance(value, str) and not value.strip():
            raise ValueError("condition threshold cannot be blank")
        return value


class PlanSymbolContext(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    symbol: str
    name: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1)
    is_holding: bool
    trend: dict[str, Any] = Field(default_factory=dict)
    daily_feature_facts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    volume_price: dict[str, Any] = Field(default_factory=dict)
    money_flow: dict[str, Any] = Field(default_factory=dict)
    conditions: list[PlanCondition] = Field(default_factory=list)
    allowed_actions: list[PlanAction] = Field(default_factory=list)
    prohibited_actions: list[PlanAction] = Field(default_factory=list)
    position_constraint: dict[str, Any] = Field(default_factory=dict)
    position_context: dict[str, Any] = Field(default_factory=dict)
    account_context: dict[str, Any] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    invalid_if: list[str] = Field(default_factory=list)
    data_quality: PlanDataQuality = "degraded"
    warnings: list[str] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_valid(cls, value: str) -> str:
        return _require_symbol(value)

    @field_validator("sources", "risks", "invalid_if", "warnings")
    @classmethod
    def text_lists_must_not_contain_blanks(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("text lists cannot contain blank values")
        return value


class MarketPlanSymbolInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    symbol: str
    name: str = Field(min_length=1)
    sources: list[str] = Field(min_length=1)
    is_holding: bool
    current_price: float = Field(gt=0, allow_inf_nan=False)
    daily_features: dict[str, Any]
    daily_feature_facts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    market_structure: dict[str, Any]
    money_flow: dict[str, Any]
    position_context: dict[str, Any] = Field(default_factory=dict)
    account_context: dict[str, Any] = Field(default_factory=dict)
    data_quality: PlanDataQuality
    warnings: list[str] = Field(default_factory=list)

    @field_validator("symbol")
    @classmethod
    def symbol_must_be_valid(cls, value: str) -> str:
        return _require_symbol(value)

    @field_validator("sources", "warnings")
    @classmethod
    def strings_must_not_be_blank(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("market plan text lists cannot contain blanks")
        return value


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
    version: int = Field(default=1, ge=1)
    source_run_id: int | str | None = None
    market_input_snapshot_id: int | None = Field(default=None, gt=0)
    data_time: datetime | None = None
    data_quality: PlanDataQuality = "degraded"
    symbol_contexts: dict[str, PlanSymbolContext] = Field(default_factory=dict)

    @field_validator("generated_at", "valid_until", "ledger_max_updated_at", "data_time")
    @classmethod
    def datetimes_must_be_timezone_aware(
        cls,
        value: datetime | None,
        info: Any,
    ) -> datetime | None:
        return _require_timezone_aware(value, info.field_name)

    @field_validator("source_run_id")
    @classmethod
    def source_run_id_must_be_valid(
        cls, value: int | str | None
    ) -> int | str | None:
        if isinstance(value, int) and value <= 0:
            raise ValueError("source_run_id integer must be positive")
        if isinstance(value, str) and not value.strip():
            raise ValueError("source_run_id cannot be blank")
        return value

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

    @model_validator(mode="after")
    def symbol_context_keys_must_match_payload_symbols(self) -> "TradingPlan":
        for symbol, context in self.symbol_contexts.items():
            _require_symbol(symbol)
            if context.symbol != symbol:
                raise ValueError("symbol context key must match context symbol")
        return self
