from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantitative_trading.strategy.models import StrategyAction
from quantitative_trading.instrument.models import InstrumentMetadata


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


class RiskContext(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    proposed_value: float = Field(default=0, ge=0, allow_inf_nan=False)
    daily_new_buy_value: float = Field(default=0, ge=0, allow_inf_nan=False)
    daily_trade_count: int = Field(default=0, ge=0)
    consecutive_losses: int = Field(default=0, ge=0)
    in_loss_cooldown: bool = False
    liquidity_amount: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    instrument: InstrumentMetadata | None = None


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


class PositionConstraint(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    suggested_quantity: int = Field(ge=0)
    suggested_value: float = Field(ge=0, allow_inf_nan=False)
    board_lot: int = Field(default=100, gt=0)
    max_position_ratio: float = Field(gt=0, le=1)
    max_total_position_ratio: float = Field(gt=0, le=1)
    max_daily_new_buy_ratio: float = Field(gt=0, le=1)
    limiting_factors: list[str] = Field(default_factory=list)

    @field_validator("suggested_quantity")
    @classmethod
    def quantity_must_use_board_lots(cls, value: int) -> int:
        if value % 100 != 0:
            raise ValueError("suggested quantity must use 100-share board lots")
        return value
