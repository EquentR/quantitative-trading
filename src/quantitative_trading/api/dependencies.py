from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import Depends, Header

from quantitative_trading.api.auth import AuthService, InvalidTokenError, TokenClaims
from quantitative_trading.api.errors import ApiError
from quantitative_trading.config import Settings
from quantitative_trading.storage.api_auth import ApiAuthRepository
from quantitative_trading.storage.sqlite import connect, migrate  # noqa: F401


@dataclass(frozen=True)
class ApiContainer:
    settings: Settings
    scheduler: object | None = None
    email_sender: object | None = None
    smtp_connection_tester: object | None = None


def get_container() -> ApiContainer:
    raise RuntimeError("container dependency not configured")


@contextmanager
def connection_scope(settings: Settings) -> Iterator[sqlite3.Connection]:
    # API 请求使用短连接，避免跨请求共享 SQLite 连接状态；schema 迁移由 app 启动阶段完成。
    with connect(settings) as connection:
        yield connection


def auth_service(settings: Settings, connection: sqlite3.Connection) -> AuthService:
    return AuthService(
        ApiAuthRepository(connection),
        token_ttl_seconds=settings.api_token_ttl_seconds,
        startup_password=settings.api_access_password,
        configured_token_secret=settings.api_token_secret,
    )


def require_token(
    authorization: str | None = Header(default=None),
    container: ApiContainer = Depends(get_container),
) -> TokenClaims:
    return verify_authorization_header(authorization, container)


def verify_authorization_header(
    authorization: str | None,
    container: ApiContainer,
) -> TokenClaims:
    if authorization is None:
        raise ApiError(
            status_code=401,
            code="unauthorized",
            message="missing bearer token",
        )

    scheme, separator, token = authorization.partition(" ")
    token = token.strip()
    if separator == "" or scheme.lower() != "bearer" or token == "":
        raise ApiError(
            status_code=401,
            code="unauthorized",
            message="missing bearer token",
        )

    try:
        with connection_scope(container.settings) as connection:
            return auth_service(container.settings, connection).verify_token(token)
    except InvalidTokenError as exc:
        raise ApiError(
            status_code=401,
            code="unauthorized",
            message="invalid token",
        ) from exc
