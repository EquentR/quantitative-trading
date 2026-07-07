from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from quantitative_trading.storage.api_auth import ApiAuthRepository


PASSWORD_HASH_ALGORITHM = "pbkdf2_sha256"
PASSWORD_HASH_ITERATIONS = 260_000
TOKEN_USER = "local"


class AuthSetupRequiredError(ValueError):
    pass


class AuthAlreadyConfiguredError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class InvalidTokenError(ValueError):
    pass


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    token_type: str
    expires_at: datetime


@dataclass(frozen=True)
class TokenClaims:
    user: str
    expires_at: datetime


class AuthService:
    def __init__(
        self,
        repository: ApiAuthRepository,
        *,
        token_ttl_seconds: int,
        startup_password: str | None = None,
        configured_token_secret: str | None = None,
    ) -> None:
        self._repository = repository
        self._token_ttl_seconds = token_ttl_seconds
        self._startup_password = startup_password
        if configured_token_secret is not None:
            self._repository.save_token_secret(
                configured_token_secret,
                now=datetime.now(UTC),
            )

    def status(self) -> str:
        state = self._repository.get()
        if state.password_hash is not None or self._startup_password is not None:
            return "configured"
        return "setup_required"

    def setup_password(self, password: str, *, now: datetime | None = None) -> None:
        saved_at = _aware_now(now)
        state = self._repository.get()
        if state.password_hash is not None:
            raise AuthAlreadyConfiguredError("api password already configured")
        self._repository.save_password_hash(_hash_password(password), now=saved_at)

    def login(self, password: str, *, now: datetime | None = None) -> LoginResult:
        issued_at = _aware_now(now)
        state = self._repository.get()
        if state.password_hash is None and self._startup_password is None:
            raise AuthSetupRequiredError("api password setup required")

        if state.password_hash is not None:
            password_is_valid = _verify_password(password, state.password_hash)
        else:
            password_is_valid = hmac.compare_digest(password, self._startup_password or "")
        if not password_is_valid:
            raise InvalidCredentialsError("invalid credentials")

        expires_at = issued_at + timedelta(seconds=self._token_ttl_seconds)
        access_token = _encode_token(
            {"user": TOKEN_USER, "exp": expires_at.isoformat()},
            secret=state.token_secret,
        )
        return LoginResult(
            access_token=access_token,
            token_type="bearer",
            expires_at=expires_at,
        )

    def verify_token(self, token: str, *, now: datetime | None = None) -> TokenClaims:
        checked_at = _aware_now(now)
        state = self._repository.get()
        payload = _decode_token(token, secret=state.token_secret)
        user = payload.get("user")
        expires_at = _parse_token_expiration(payload.get("exp"))
        if user != TOKEN_USER or expires_at <= checked_at:
            raise InvalidTokenError("invalid token")
        return TokenClaims(user=TOKEN_USER, expires_at=expires_at)


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    # PBKDF2 使用随机盐和较高迭代次数，只持久化哈希结果，不保存明文密码。
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"{PASSWORD_HASH_ALGORITHM}${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, raw_iterations, salt, expected_digest = password_hash.split("$", 3)
        iterations = int(raw_iterations)
    except (TypeError, ValueError):
        return False
    if algorithm != PASSWORD_HASH_ALGORITHM or iterations <= 0:
        return False
    try:
        # 用同一盐重新计算哈希，并用恒定时间比较降低时序侧信道风险。
        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("ascii"),
            iterations,
        ).hex()
    except (OverflowError, ValueError):
        return False
    return hmac.compare_digest(actual_digest, expected_digest)


def _encode_token(payload: dict[str, Any], *, secret: str) -> str:
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = _base64url_encode(payload_json)
    # token 只签名不加密；签名覆盖 payload，验证端用同一 secret 检测篡改。
    signature = hmac.new(
        secret.encode("utf-8"),
        payload_part.encode("ascii"),
        hashlib.sha256,
    ).digest()
    signature_part = _base64url_encode(signature)
    return f"{payload_part}.{signature_part}"


def _decode_token(token: str, *, secret: str) -> dict[str, Any]:
    try:
        payload_part, signature_part = token.split(".", 1)
        payload_bytes = _base64url_decode(payload_part)
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise InvalidTokenError("invalid token") from exc
    expected_signature = hmac.new(
        secret.encode("utf-8"),
        payload_part.encode("ascii"),
        hashlib.sha256,
    ).digest()
    expected_part = _base64url_encode(expected_signature)
    # 验签失败统一报非法 token，不暴露签名密钥、payload 或具体解析细节。
    try:
        if not hmac.compare_digest(signature_part, expected_part):
            raise InvalidTokenError("invalid token")
    except TypeError as exc:
        raise InvalidTokenError("invalid token") from exc
    if not isinstance(payload, dict):
        raise InvalidTokenError("invalid token")
    return payload


def _parse_token_expiration(value: object) -> datetime:
    if not isinstance(value, str):
        raise InvalidTokenError("invalid token")
    try:
        expires_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidTokenError("invalid token") from exc
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise InvalidTokenError("invalid token")
    return expires_at


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))


def _aware_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("auth timestamps must be timezone-aware")
    return value
