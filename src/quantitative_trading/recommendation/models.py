from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantitative_trading.strategy.models import PROFIT_PROMISE_PHRASES
from quantitative_trading.instrument.models import InstrumentMetadata


Confidence = Literal["low", "medium", "high"]
DATA_REFERENCE_NAMES = (
    "ledger",
    "account",
    "quote",
    "history",
    "money_flow",
    "intraday",
    "plan",
)


def _missing_data_references() -> dict[str, dict[str, str]]:
    return {name: {"status": "missing"} for name in DATA_REFERENCE_NAMES}


class RecommendationAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    ADD = "add"
    REDUCE = "reduce"
    HOLD = "hold"
    WATCH = "watch"
    AVOID = "avoid"


CONSTRUCTIVE_ACTIONS = {
    RecommendationAction.BUY,
    RecommendationAction.ADD,
    RecommendationAction.HOLD,
    RecommendationAction.WATCH,
}


def _require_timezone_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _reject_profit_promises(value: str, field_name: str) -> str:
    if any(phrase in value for phrase in PROFIT_PROMISE_PHRASES):
        raise ValueError(f"{field_name} cannot contain profit promises")
    return value


def _require_text_list(values: list[str], field_name: str) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"{field_name} cannot contain empty text")
        cleaned.append(_reject_profit_promises(stripped, field_name))
    return cleaned


class Recommendation(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    recommendation_id: str = Field(min_length=1)
    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    instrument: InstrumentMetadata | None = None
    action: RecommendationAction
    confidence: Confidence
    position_context: dict[str, Any]
    account_context: dict[str, Any]
    price_context: dict[str, Any]
    reason: list[str] = Field(min_length=1)
    risk: dict[str, Any]
    valid_until: datetime
    data_time: datetime
    fetched_at: datetime | None = None
    created_at: datetime | None = None
    run_id: int | str | None = None
    market_input_snapshot_id: int | None = Field(default=None, gt=0)
    plan_id: str | None = None
    plan_version: int | str | None = None
    decision_cycle: str | None = Field(default=None, min_length=1)
    condition_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    condition_fingerprint_version: int | None = Field(default=None, ge=1)
    dedup_key: str | None = Field(default=None, min_length=1, max_length=500)
    audit_id: str | None = Field(default=None, min_length=1)
    condition_context: dict[str, Any] = Field(default_factory=dict)
    data_references: dict[str, dict[str, Any]] = Field(
        default_factory=_missing_data_references
    )
    data_quality: dict[str, Any] = Field(
        default_factory=lambda: {"overall": "degraded", "warnings": ["input trace unavailable"]}
    )
    position_constraint: dict[str, Any] = Field(default_factory=dict)

    @field_validator("reason")
    @classmethod
    def reason_must_be_explanatory(cls, value: list[str]) -> list[str]:
        return _require_text_list(value, "reason")

    @field_validator("valid_until", "data_time", "fetched_at", "created_at")
    @classmethod
    def datetimes_must_be_timezone_aware(
        cls, value: datetime | None, info
    ) -> datetime | None:
        if value is None:
            return None
        return _require_timezone_aware(value, info.field_name)

    @field_validator("plan_id")
    @classmethod
    def plan_id_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("plan_id cannot be blank")
        return value

    @field_validator("run_id")
    @classmethod
    def run_id_must_be_valid(cls, value: int | str | None) -> int | str | None:
        if isinstance(value, int) and value <= 0:
            raise ValueError("run_id integer must be positive")
        if isinstance(value, str) and not value.strip():
            raise ValueError("run_id cannot be blank")
        return value

    @field_validator("plan_version")
    @classmethod
    def plan_version_must_be_valid(cls, value: int | str | None) -> int | str | None:
        if isinstance(value, int) and value <= 0:
            raise ValueError("plan_version integer must be positive")
        if isinstance(value, str) and not value.strip():
            raise ValueError("plan_version cannot be blank")
        return value

    @model_validator(mode="after")
    def instrument_symbol_must_match(self) -> "Recommendation":
        if self.instrument is not None and self.instrument.symbol != self.symbol:
            raise ValueError("symbol must match instrument symbol")
        return self

    @model_validator(mode="after")
    def constructive_outputs_must_have_invalidation(self) -> "Recommendation":
        self.risk = dict(self.risk)
        invalid_if = self.risk.get("invalid_if")
        if self.action in CONSTRUCTIVE_ACTIONS:
            if not isinstance(invalid_if, list) or not invalid_if:
                raise ValueError("risk.invalid_if is required for buy/add/hold/watch recommendations")

        if isinstance(invalid_if, list):
            self.risk["invalid_if"] = _require_text_list(invalid_if, "risk.invalid_if")

        notes = self.risk.get("notes")
        if isinstance(notes, list):
            self.risk["notes"] = _require_text_list(notes, "risk.notes")

        references = {
            name: dict(self.data_references.get(name, {"status": "missing"}))
            for name in DATA_REFERENCE_NAMES
        }
        for name, reference in references.items():
            status = reference.get("status")
            if not isinstance(status, str) or not status.strip():
                raise ValueError(f"data_references.{name}.status is required")
        self.data_references = references

        return self
