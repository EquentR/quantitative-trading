from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, field_validator

from quantitative_trading.datasource.credentials import redact_secret


EASTMONEY_PROVIDER = "eastmoney"


class DatasourceCredentialStatus(StrEnum):
    CONFIGURED = "configured"
    MISSING = "missing"
    INVALID = "invalid"


class DatasourceStatus(BaseModel):
    provider: str
    status: DatasourceCredentialStatus
    last_checked_at: datetime | None = None
    last_error: str | None = None
    updated_at: datetime

    @field_validator("last_checked_at", "updated_at")
    @classmethod
    def timestamps_must_be_timezone_aware(
        cls,
        value: datetime | None,
        info: Any,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError(f"{info.field_name} must be timezone-aware")
        return value


@dataclass(frozen=True)
class DatasourceCredential:
    provider: str
    stored_secret: str = field(repr=False)
    status: DatasourceCredentialStatus
    last_checked_at: datetime | None
    last_error: str | None
    updated_at: datetime


def current_time() -> datetime:
    return datetime.now(UTC)


def _ensure_timezone_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datasource timestamps must be timezone-aware")


class DatasourceCredentialsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, provider: str) -> DatasourceCredential | None:
        row = self.connection.execute(
            """
            SELECT
              provider,
              encrypted_secret AS stored_secret,
              status,
              last_checked_at,
              last_error,
              updated_at
            FROM datasource_credentials
            WHERE provider = ?
            """,
            (provider,),
        ).fetchone()
        if row is None:
            return None
        return self._from_row(row)

    def save_secret(
        self,
        provider: str,
        secret: str,
        *,
        now: datetime,
    ) -> DatasourceCredential:
        _ensure_timezone_aware(now)
        if redact_secret(secret) == DatasourceCredentialStatus.MISSING.value:
            raise ValueError("datasource secret must not be blank")
        status = DatasourceCredentialStatus(redact_secret(secret))
        self.connection.execute(
            # The column name is fixed by the P1 roadmap schema; Python code uses
            # stored_secret so only redacted status leaves this module.
            """
            INSERT INTO datasource_credentials (
              provider,
              encrypted_secret,
              status,
              last_checked_at,
              last_error,
              updated_at
            ) VALUES (
              :provider,
              :stored_secret,
              :status,
              NULL,
              NULL,
              :updated_at
            )
            ON CONFLICT(provider) DO UPDATE SET
              encrypted_secret = excluded.encrypted_secret,
              status = excluded.status,
              last_checked_at = excluded.last_checked_at,
              last_error = excluded.last_error,
              updated_at = excluded.updated_at
            """,
            {
                "provider": provider,
                "stored_secret": secret,
                "status": status.value,
                "updated_at": now.isoformat(),
            },
        )
        self.connection.commit()
        saved = self.get(provider)
        if saved is None:
            raise RuntimeError("datasource credential was not saved")
        return saved

    def clear_secret(self, provider: str, *, now: datetime) -> DatasourceStatus:
        _ensure_timezone_aware(now)
        self.connection.execute(
            "DELETE FROM datasource_credentials WHERE provider = ?",
            (provider,),
        )
        self.connection.commit()
        return DatasourceStatus(
            provider=provider,
            status=DatasourceCredentialStatus.MISSING,
            last_checked_at=None,
            last_error=None,
            updated_at=now,
        )

    def record_check(self, provider: str, *, now: datetime) -> DatasourceCredential:
        _ensure_timezone_aware(now)
        existing = self.get(provider)
        secret = "" if existing is None else existing.stored_secret
        status = DatasourceCredentialStatus(redact_secret(secret))
        self.connection.execute(
            # The column name is fixed by the P1 roadmap schema; Python code uses
            # stored_secret so only redacted status leaves this module.
            """
            INSERT INTO datasource_credentials (
              provider,
              encrypted_secret,
              status,
              last_checked_at,
              last_error,
              updated_at
            ) VALUES (
              :provider,
              :stored_secret,
              :status,
              :last_checked_at,
              NULL,
              :updated_at
            )
            ON CONFLICT(provider) DO UPDATE SET
              encrypted_secret = excluded.encrypted_secret,
              status = excluded.status,
              last_checked_at = excluded.last_checked_at,
              last_error = excluded.last_error,
              updated_at = excluded.updated_at
            """,
            {
                "provider": provider,
                "stored_secret": secret,
                "status": status.value,
                "last_checked_at": now.isoformat(),
                "updated_at": now.isoformat(),
            },
        )
        self.connection.commit()
        checked = self.get(provider)
        if checked is None:
            raise RuntimeError("datasource credential check was not saved")
        return checked

    def _from_row(self, row: sqlite3.Row) -> DatasourceCredential:
        last_checked_at = row["last_checked_at"]
        return DatasourceCredential(
            provider=row["provider"],
            stored_secret=row["stored_secret"],
            status=DatasourceCredentialStatus(row["status"]),
            last_checked_at=(
                None if last_checked_at is None else datetime.fromisoformat(last_checked_at)
            ),
            last_error=row["last_error"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


class DatasourceStatusService:
    def __init__(
        self,
        repository: DatasourceCredentialsRepository,
        *,
        now: datetime | None = None,
    ) -> None:
        self._repository = repository
        self._now = now

    def get_status(self, provider: str = EASTMONEY_PROVIDER) -> DatasourceStatus:
        credential = self._repository.get(provider)
        if credential is None:
            return DatasourceStatus(
                provider=provider,
                status=DatasourceCredentialStatus.MISSING,
                last_checked_at=None,
                last_error=None,
                updated_at=self._current_time(),
            )
        return self._redacted_status(credential)

    def set_key(self, api_key: str, provider: str = EASTMONEY_PROVIDER) -> DatasourceStatus:
        credential = self._repository.save_secret(
            provider,
            api_key,
            now=self._current_time(),
        )
        return self._redacted_status(credential)

    def delete_key(self, provider: str = EASTMONEY_PROVIDER) -> DatasourceStatus:
        return self._repository.clear_secret(provider, now=self._current_time())

    def check(self, provider: str = EASTMONEY_PROVIDER) -> DatasourceStatus:
        credential = self._repository.record_check(provider, now=self._current_time())
        return self._redacted_status(credential)

    def _redacted_status(self, credential: DatasourceCredential) -> DatasourceStatus:
        status = credential.status
        if redact_secret(credential.stored_secret) == DatasourceCredentialStatus.MISSING.value:
            status = DatasourceCredentialStatus.MISSING
        return DatasourceStatus(
            provider=credential.provider,
            status=status,
            last_checked_at=credential.last_checked_at,
            last_error=credential.last_error,
            updated_at=credential.updated_at,
        )

    def _current_time(self) -> datetime:
        now = self._now or current_time()
        _ensure_timezone_aware(now)
        return now
