from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from quantitative_trading.api.auth import (
    AuthAlreadyConfiguredError,
    AuthSetupRequiredError,
    InvalidCredentialsError,
    TokenClaims,
)
from quantitative_trading.api.dependencies import (
    ApiContainer,
    auth_service,
    connection_scope,
    get_container,
    require_token,
)
from quantitative_trading.api.errors import ApiError


router = APIRouter(prefix="/auth", tags=["auth"])


class PasswordRequest(BaseModel):
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_at: datetime


@router.post("/setup-password")
def setup_password(
    payload: PasswordRequest,
    container: ApiContainer = Depends(get_container),
) -> dict[str, str]:
    try:
        with connection_scope(container.settings) as connection:
            auth_service(container.settings, connection).setup_password(payload.password)
    except AuthAlreadyConfiguredError as exc:
        raise ApiError(
            status_code=409,
            code="auth_already_configured",
            message="api password already configured",
        ) from exc
    return {"auth_status": "configured"}


@router.post("/login")
def login(
    payload: PasswordRequest,
    container: ApiContainer = Depends(get_container),
) -> LoginResponse:
    try:
        with connection_scope(container.settings) as connection:
            result = auth_service(container.settings, connection).login(payload.password)
    except AuthSetupRequiredError as exc:
        raise ApiError(
            status_code=403,
            code="auth_setup_required",
            message="api password setup required",
        ) from exc
    except InvalidCredentialsError as exc:
        raise ApiError(
            status_code=401,
            code="unauthorized",
            message="invalid credentials",
        ) from exc
    return LoginResponse(
        access_token=result.access_token,
        token_type=result.token_type,
        expires_at=result.expires_at,
    )


@router.post("/logout")
def logout() -> dict[str, str]:
    # 首版 token 无服务端撤销表，logout 仅确认前端应丢弃本地 bearer token。
    return {"status": "ok"}


@router.get("/me")
def me(claims: TokenClaims = Depends(require_token)) -> dict[str, str]:
    return {"user": claims.user}
