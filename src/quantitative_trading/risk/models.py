from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantitative_trading.strategy.models import StrategyAction


class RiskConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    single_position_limit: float = Field(default=0.30, gt=0, le=1)
    total_position_limit: float = Field(default=0.80, gt=0, le=1)
    daily_new_buy_limit: float = Field(default=0.20, gt=0, le=1)
    daily_trade_count_limit: int = Field(default=3, ge=1)
    first_watch_position_min: float = Field(default=0.10, gt=0, le=1)
    first_watch_position_max: float = Field(default=0.15, gt=0, le=1)
    loss_cooldown_count: int = Field(default=2, ge=1)
    loss_cooldown_trading_days: int = Field(default=1, ge=1)
    liquidity_amount_threshold: float = Field(default=0, ge=0)


class RiskDecision(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    allowed: bool
    original_action: StrategyAction
    action: StrategyAction
    reasons: list[str] = Field(default_factory=list)

    @field_validator("reasons")
    @classmethod
    def reasons_must_be_readable(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for reason in value:
            stripped = reason.strip()
            if not stripped:
                raise ValueError("risk reasons cannot contain empty text")
            cleaned.append(stripped)
        return cleaned
