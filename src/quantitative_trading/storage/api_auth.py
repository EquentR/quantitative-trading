from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class ApiAuthState:
    # 只保存密码哈希；明文密码必须在调用方完成哈希后才能传入 repository。
    password_hash: str | None = field(repr=False)
    # token_secret 是本地签名密钥，repr 中必须隐藏，避免误写入日志。
    token_secret: str = field(repr=False)
    updated_at: datetime

    @property
    def is_configured(self) -> bool:
        return self.password_hash is not None


class ApiAuthRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self) -> ApiAuthState:
        row = self._fetch()
        if row is None:
            # 首次读取时生成本地 token secret，避免把固定密钥写入仓库或配置示例。
            # 该密钥明文保存在本机 SQLite 中，部署时应依赖文件权限限制访问。
            token_secret = secrets.token_urlsafe(32)
            now = datetime.now(UTC)
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO api_auth_state (
                      id,
                      password_hash,
                      token_secret,
                      updated_at
                    ) VALUES (
                      1,
                      NULL,
                      ?,
                      ?
                    )
                    """,
                    (token_secret, now.isoformat()),
                )
            row = self._fetch()
        if row is None:
            raise RuntimeError("api auth state was not initialized")
        return self._from_row(row)

    def save_password_hash(self, password_hash: str, *, now: datetime) -> ApiAuthState:
        current = self.get()
        # 更新密码哈希时保留 token secret，避免让现有签名状态意外失效。
        with self.connection:
            self.connection.execute(
                """
                UPDATE api_auth_state
                SET password_hash = ?, updated_at = ?
                WHERE id = 1
                """,
                (password_hash, now.isoformat()),
            )
        return ApiAuthState(
            password_hash=password_hash,
            token_secret=current.token_secret,
            updated_at=now,
        )

    def save_token_secret(self, token_secret: str, *, now: datetime) -> ApiAuthState:
        current = self.get()
        # 轮换 token secret 时保留密码哈希，认证配置状态不应被清空。
        with self.connection:
            self.connection.execute(
                """
                UPDATE api_auth_state
                SET token_secret = ?, updated_at = ?
                WHERE id = 1
                """,
                (token_secret, now.isoformat()),
            )
        return ApiAuthState(
            password_hash=current.password_hash,
            token_secret=token_secret,
            updated_at=now,
        )

    def _fetch(self) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT password_hash, token_secret, updated_at
            FROM api_auth_state
            WHERE id = 1
            """
        ).fetchone()

    def _from_row(self, row: sqlite3.Row) -> ApiAuthState:
        return ApiAuthState(
            password_hash=row["password_hash"],
            token_secret=row["token_secret"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
