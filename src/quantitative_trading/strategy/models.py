from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Confidence = Literal["low", "medium", "high"]

PROFIT_PROMISE_PHRASES = (
    "保证收益",
    "保证正收益",
    "确定收益",
    "确定性收益",
    "稳赚",
    "必赚",
    "无风险收益",
)


class StrategyAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    ADD = "add"
    REDUCE = "reduce"
    HOLD = "hold"
    WATCH = "watch"
    AVOID = "avoid"


def _require_explanatory_strings(values: list[str], field_name: str) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        stripped = value.strip()
        if not stripped:
            raise ValueError(f"{field_name} cannot contain empty text")
        if any(phrase in stripped for phrase in PROFIT_PROMISE_PHRASES):
            raise ValueError(f"{field_name} cannot contain profit promises")
        cleaned.append(stripped)
    return cleaned


class StrategySignal(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    action: StrategyAction
    confidence: Confidence
    machine_reason: list[str] = Field(min_length=1)
    human_reason: list[str] = Field(min_length=1)
    invalid_if: list[str] = Field(min_length=1)

    @field_validator("machine_reason", "human_reason", "invalid_if")
    @classmethod
    def explanation_lists_must_be_non_empty(cls, value: list[str], info) -> list[str]:
        return _require_explanatory_strings(value, info.field_name)
