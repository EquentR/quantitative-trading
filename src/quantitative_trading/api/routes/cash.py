from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.cash.models import CashAccount, CashTransaction
from quantitative_trading.cash.repository import (
    CashAccountNotInitializedError,
    CashAccountRepository,
)
from quantitative_trading.cash.service import CashService, CashTransferError


# 资金 API 只维护手动资金账户台账，不代表真实银行或券商资金变动。
router = APIRouter(
    prefix="/cash",
    tags=["cash"],
    dependencies=[Depends(require_token)],
)


class InitializeCashRequest(BaseModel):
    cash: float = Field(gt=0)
    note: str = "initial principal"


class CashTransferRequest(BaseModel):
    type: Literal["transfer_in", "transfer_out"]
    amount: float = Field(gt=0)
    note: str = ""


class CashAdjustmentRequest(BaseModel):
    cash: float = Field(ge=0)
    note: str


def _account_not_initialized() -> ApiError:
    return ApiError(
        status_code=404,
        code="cash_account_not_initialized",
        message="cash account not initialized",
    )


def _cash_transfer_invalid(message: str) -> ApiError:
    return ApiError(
        status_code=422,
        code="cash_transfer_invalid",
        message=message,
    )


@router.get("/account", response_model=CashAccount)
def get_cash_account(container: ApiContainer = Depends(get_container)) -> CashAccount:
    with connection_scope(container.settings) as connection:
        service = CashService(CashAccountRepository(connection))
        account = service.get_account()

    if account is None:
        raise _account_not_initialized()
    return account


@router.post("/account", response_model=CashAccount, status_code=201)
def initialize_cash_account(
    payload: InitializeCashRequest,
    container: ApiContainer = Depends(get_container),
) -> CashAccount:
    try:
        with connection_scope(container.settings) as connection:
            service = CashService(CashAccountRepository(connection))
            return service.initialize(payload.cash, note=payload.note)
    except CashTransferError as exc:
        raise ApiError(
            status_code=409,
            code="cash_account_already_initialized",
            message="cash account already initialized",
        ) from exc


@router.post("/transfers", response_model=CashAccount)
def create_cash_transfer(
    payload: CashTransferRequest,
    container: ApiContainer = Depends(get_container),
) -> CashAccount:
    try:
        with connection_scope(container.settings) as connection:
            service = CashService(CashAccountRepository(connection))
            if payload.type == "transfer_in":
                return service.transfer_in(payload.amount, note=payload.note)
            return service.transfer_out(payload.amount, note=payload.note)
    except CashAccountNotInitializedError as exc:
        raise _account_not_initialized() from exc
    except CashTransferError as exc:
        raise _cash_transfer_invalid(str(exc)) from exc


@router.post("/adjustments", response_model=CashAccount)
def adjust_cash_account(
    payload: CashAdjustmentRequest,
    container: ApiContainer = Depends(get_container),
) -> CashAccount:
    try:
        with connection_scope(container.settings) as connection:
            service = CashService(CashAccountRepository(connection))
            return service.adjust_cash(payload.cash, note=payload.note)
    except CashAccountNotInitializedError as exc:
        raise _account_not_initialized() from exc
    except CashTransferError as exc:
        raise _cash_transfer_invalid(str(exc)) from exc


@router.get("/transactions", response_model=list[CashTransaction])
def list_cash_transactions(
    limit: Annotated[int, Query(gt=0)] = 20,
    container: ApiContainer = Depends(get_container),
) -> list[CashTransaction]:
    with connection_scope(container.settings) as connection:
        service = CashService(CashAccountRepository(connection))
        return service.list_transactions(limit=limit)
