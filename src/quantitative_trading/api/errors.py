from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from quantitative_trading.sanitization import redact_sensitive_text


SENSITIVE_DETAIL_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }
)
REDACTED_VALUE = "[redacted]"


class ApiError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}


def error_body(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sanitized_details = _sanitize_details(details or {})
    return {
        "error": {
            "code": code,
            "message": message,
            "details": jsonable_encoder(sanitized_details),
        }
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code=exc.code,
                message=exc.message,
                details=exc.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                code="validation_error",
                message="request validation failed",
                details={"errors": _sanitize_validation_errors(exc.errors())},
            ),
        )


def _sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized_errors: list[dict[str, Any]] = []
    for error in errors:
        sanitized = dict(error)
        sanitized.pop("input", None)
        sanitized_errors.append(_sanitize_details(sanitized))
    return sanitized_errors


def _sanitize_details(value: Any, *, current_key: str | None = None) -> Any:
    # 统一错误响应会进入前端和日志链路，敏感字段按 key 递归脱敏，避免回显密码或 token。
    if current_key is not None and _is_sensitive_key(current_key):
        return REDACTED_VALUE
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_details(item, current_key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_sanitize_details(item, current_key=current_key) for item in value]
    if (
        isinstance(value, str)
        and current_key in {"replacement", "preview", "selection"}
        and value.startswith("/api/v1/")
    ):
        return value
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    parts = frozenset(part for part in normalized.split("_") if part)
    return (
        normalized in SENSITIVE_DETAIL_KEYS
        or normalized.endswith("_secret")
        or bool(parts & SENSITIVE_DETAIL_KEYS)
    )
