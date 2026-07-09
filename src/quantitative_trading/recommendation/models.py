from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quantitative_trading.strategy.models import PROFIT_PROMISE_PHRASES


Confidence = Literal["low", "medium", "high"]


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
    action: RecommendationAction
    confidence: Confidence
    position_context: dict[str, Any]
    account_context: dict[str, Any]
    price_context: dict[str, Any]
    reason: list[str] = Field(min_length=1)
    risk: dict[str, Any]
    valid_until: datetime
    data_time: datetime

    @field_validator("reason")
    @classmethod
    def reason_must_be_explanatory(cls, value: list[str]) -> list[str]:
        return _require_text_list(value, "reason")

    @field_validator("valid_until", "data_time")
    @classmethod
    def datetimes_must_be_timezone_aware(cls, value: datetime, info) -> datetime:
        return _require_timezone_aware(value, info.field_name)

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

        return self
