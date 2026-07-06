from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PositionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    symbol: str = Field(pattern=r"^\d{6}$")
    name: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    available_quantity: int = Field(ge=0)
    cost_price: float = Field(gt=0)
    opened_at: date
    note: str = ""

    @model_validator(mode="after")
    def available_quantity_cannot_exceed_quantity(self) -> "PositionInput":
        if self.available_quantity > self.quantity:
            raise ValueError("available_quantity cannot exceed quantity")
        return self


class Position(PositionInput):
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must be timezone-aware")
        return value
