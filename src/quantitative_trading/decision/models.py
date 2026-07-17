from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantitative_trading.instrument.models import InstrumentMetadata


DatasetDecisionStatus = Literal[
    "complete",
    "degraded",
    "failed",
    "stale",
    "missing",
]
PlanDecisionStatus = Literal[
    "active",
    "missing",
    "draft",
    "superseded",
    "expired",
    "stale",
]


class DecisionSymbolInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    instrument: InstrumentMetadata | None = None
    is_holding: bool
    current_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    support_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    stop_loss_price: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    short_ma: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    plan_id: str | None = None
    plan_active: bool
    plan_allows_entry: bool
    plan_condition_met: bool
    daily_structure_confirmed: bool
    intraday_strength: Literal["strong", "neutral", "weak"]
    money_flow_confirmed: bool | None
    data_quality: Literal["complete", "degraded", "failed", "stale"]
    quote_status: DatasetDecisionStatus
    quote_usable: bool
    history_status: DatasetDecisionStatus
    history_usable: bool
    intraday_status: DatasetDecisionStatus
    intraday_usable: bool
    plan_status: PlanDecisionStatus
    trading_status: Literal["normal", "suspended", "unknown"] = "unknown"
    limit_status: Literal["none", "up", "down", "unknown"] = "unknown"
    position_context: dict[str, Any]
    account_context: dict[str, Any]
    price_context: dict[str, Any]
    data_references: dict[str, dict[str, Any]]
    invalid_if: list[str] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    data_time_source: Literal["market", "instrument_metadata"] = "market"
    data_time: datetime
    fetched_at: datetime
    valid_until: datetime
    run_id: int | str
    market_input_snapshot_id: int = Field(gt=0)

    @field_validator("data_time", "fetched_at", "valid_until")
    @classmethod
    def times_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("decision times must be timezone-aware")
        return value

    @field_validator("plan_id")
    @classmethod
    def plan_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("plan_id cannot be blank")
        return value

    @field_validator("run_id")
    @classmethod
    def run_id_must_be_valid(cls, value: int | str) -> int | str:
        if isinstance(value, int) and value <= 0:
            raise ValueError("run_id integer must be positive")
        if isinstance(value, str) and not value.strip():
            raise ValueError("run_id cannot be blank")
        return value

    @field_validator("invalid_if", "warnings")
    @classmethod
    def text_lists_must_not_contain_blanks(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("decision text lists cannot contain blank values")
        return value

    @model_validator(mode="after")
    def instrument_symbol_must_match(self) -> "DecisionSymbolInput":
        if self.instrument is not None and self.instrument.symbol != self.symbol:
            raise ValueError("symbol must match instrument symbol")
        if self.quote_usable and (
            self.quote_status not in {"complete", "degraded"}
            or self.current_price is None
        ):
            raise ValueError("usable quote requires valid status and current price")
        if self.history_usable and self.history_status not in {
            "complete",
            "degraded",
        }:
            raise ValueError("usable history requires complete or degraded status")
        if self.intraday_usable and self.intraday_status != "complete":
            raise ValueError("usable intraday strength requires complete status")
        return self
