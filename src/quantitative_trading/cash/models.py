from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator


class CashTransactionType(StrEnum):
    INITIAL_DEPOSIT = "initial_deposit"
    TRANSFER_IN = "transfer_in"
    TRANSFER_OUT = "transfer_out"
    CASH_ADJUSTMENT = "cash_adjustment"


def _must_be_timezone_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value


class CashAccount(BaseModel):
    cash_balance: float = Field(ge=0)
    total_transfer_in: float = Field(ge=0)
    total_transfer_out: float = Field(ge=0)
    updated_at: datetime

    @computed_field
    @property
    def net_principal(self) -> float:
        return self.total_transfer_in - self.total_transfer_out

    @field_validator("updated_at")
    @classmethod
    def updated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _must_be_timezone_aware(value)

    @model_validator(mode="after")
    def transfer_out_cannot_exceed_transfer_in(self) -> "CashAccount":
        if self.total_transfer_out > self.total_transfer_in:
            raise ValueError("total_transfer_out cannot exceed total_transfer_in")
        return self


class CashTransaction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int | None = None
    type: CashTransactionType
    amount: float = Field(gt=0)
    cash_before: float = Field(ge=0)
    cash_after: float = Field(ge=0)
    occurred_at: datetime
    note: str = ""

    @field_validator("occurred_at")
    @classmethod
    def occurred_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        return _must_be_timezone_aware(value)
