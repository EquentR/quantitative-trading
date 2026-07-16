from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from quantitative_trading.instrument.models import InstrumentMetadata, InstrumentPreview
from quantitative_trading.sanitization import safe_error_summary


class InstrumentCatalogStatus(StrEnum):
    COMPLETE = "complete"
    STALE = "stale"
    FAILED = "failed"


class InstrumentCatalogState(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source: str = Field(min_length=1)
    last_attempt_at: datetime
    last_success_at: datetime | None = None
    data_trade_date: date | None = None
    status: InstrumentCatalogStatus
    last_error: str = ""
    warnings: list[str] = Field(default_factory=list)
    updated_at: datetime

    @field_validator("last_attempt_at", "last_success_at", "updated_at")
    @classmethod
    def times_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("catalog timestamps must be timezone-aware")
        return value


class InstrumentPreviewNotFoundError(LookupError):
    pass


class InstrumentPreviewExpiredError(LookupError):
    pass


class InstrumentRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def replace_catalog(
        self,
        items: list[InstrumentMetadata],
        *,
        commit: bool = True,
    ) -> list[InstrumentMetadata]:
        validated = self._unique(items)
        if commit:
            with self.connection:
                self._replace_catalog(validated)
        else:
            self._replace_catalog(validated)
        return self.list_active()

    def _replace_catalog(self, validated: list[InstrumentMetadata]) -> None:
        self.connection.execute("UPDATE instruments SET is_active = 0")
        for item in validated:
            self.connection.execute(
                    """
                    INSERT INTO instruments (
                      symbol, name, exchange, instrument_type, settlement_cycle,
                      price_limit_ratio, metadata_source, metadata_checked_at,
                      rule_version, is_active, warnings_json
                    ) VALUES (
                      :symbol, :name, :exchange, :instrument_type, :settlement_cycle,
                      :price_limit_ratio, :metadata_source, :metadata_checked_at,
                      :rule_version, 1, :warnings_json
                    )
                    ON CONFLICT(symbol) DO UPDATE SET
                      name=excluded.name,
                      exchange=excluded.exchange,
                      instrument_type=excluded.instrument_type,
                      settlement_cycle=excluded.settlement_cycle,
                      price_limit_ratio=excluded.price_limit_ratio,
                      metadata_source=excluded.metadata_source,
                      metadata_checked_at=excluded.metadata_checked_at,
                      rule_version=excluded.rule_version,
                      is_active=1,
                      warnings_json=excluded.warnings_json
                    """,
                self._to_row(item),
            )
        self.connection.execute(
            """
            UPDATE watch_pinned
            SET plan_enabled = 0
            WHERE plan_enabled = 1
              AND NOT EXISTS (
                SELECT 1
                FROM instruments
                WHERE instruments.symbol = watch_pinned.symbol
                  AND instruments.is_active = 1
                  AND instruments.instrument_type IN ('a_share', 'etf')
                  AND instruments.settlement_cycle IN ('t0', 't1')
              )
            """
        )

    def get(
        self,
        symbol: str,
        *,
        include_inactive: bool = False,
    ) -> InstrumentMetadata | None:
        row = self.connection.execute(
            "SELECT * FROM instruments WHERE symbol=?"
            + ("" if include_inactive else " AND is_active=1"),
            (symbol,),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def list_active(self) -> list[InstrumentMetadata]:
        rows = self.connection.execute(
            "SELECT * FROM instruments WHERE is_active=1 ORDER BY symbol"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def search(self, query: str, *, limit: int = 50) -> list[InstrumentMetadata]:
        normalized = query.strip()
        if not normalized or limit < 1:
            return []
        escaped = normalized.replace("!", "!!").replace("%", "!%").replace("_", "!_")
        prefix = escaped + "%"
        contains = "%" + escaped + "%"
        rows = self.connection.execute(
            """
            SELECT *
            FROM instruments
            WHERE is_active=1 AND (
              symbol LIKE ? ESCAPE '!' OR name LIKE ? ESCAPE '!'
            )
            ORDER BY
              CASE
                WHEN symbol = ? THEN 0
                WHEN symbol LIKE ? ESCAPE '!' THEN 1
                WHEN name = ? THEN 2
                WHEN name LIKE ? ESCAPE '!' THEN 3
                ELSE 4
              END,
              symbol
            LIMIT ?
            """,
            (
                prefix,
                contains,
                normalized,
                prefix,
                normalized,
                prefix,
                limit,
            ),
        ).fetchall()
        return [self._from_row(row) for row in rows]

    @staticmethod
    def _unique(items: list[InstrumentMetadata]) -> list[InstrumentMetadata]:
        validated = [InstrumentMetadata.model_validate(item) for item in items]
        symbols = [item.symbol for item in validated]
        if len(symbols) != len(set(symbols)):
            raise ValueError("instrument catalog contains duplicate symbols")
        return validated

    @staticmethod
    def _to_row(item: InstrumentMetadata) -> dict[str, object]:
        return {
            "symbol": item.symbol,
            "name": item.name,
            "exchange": None if item.exchange is None else item.exchange.value,
            "instrument_type": item.instrument_type.value,
            "settlement_cycle": item.settlement_cycle.value,
            "price_limit_ratio": item.price_limit_ratio,
            "metadata_source": item.metadata_source,
            "metadata_checked_at": item.metadata_checked_at.isoformat(),
            "rule_version": item.rule_version,
            "warnings_json": json.dumps(item.warnings, ensure_ascii=False),
        }

    @staticmethod
    def _from_row(row: sqlite3.Row) -> InstrumentMetadata:
        return InstrumentMetadata.model_validate(
            {
                "symbol": row["symbol"],
                "name": row["name"],
                "exchange": row["exchange"],
                "instrument_type": row["instrument_type"],
                "settlement_cycle": row["settlement_cycle"],
                "price_limit_ratio": row["price_limit_ratio"],
                "metadata_source": row["metadata_source"],
                "metadata_checked_at": row["metadata_checked_at"],
                "rule_version": row["rule_version"],
                "warnings": json.loads(row["warnings_json"]),
            }
        )


class InstrumentCatalogStateRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(
        self,
        state: InstrumentCatalogState,
        *,
        commit: bool = True,
    ) -> InstrumentCatalogState:
        state = InstrumentCatalogState.model_validate(state)
        state = state.model_copy(
            update={
                "last_error": (
                    safe_error_summary(RuntimeError(state.last_error))
                    if state.last_error
                    else ""
                ),
                "warnings": [
                    safe_error_summary(RuntimeError(warning))
                    for warning in state.warnings
                ],
            }
        )
        self.connection.execute(
            """
            INSERT INTO instrument_catalog_state (
              source, last_attempt_at, last_success_at, data_trade_date,
              status, last_error, warnings_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
              last_attempt_at=excluded.last_attempt_at,
              last_success_at=excluded.last_success_at,
              data_trade_date=excluded.data_trade_date,
              status=excluded.status,
              last_error=excluded.last_error,
              warnings_json=excluded.warnings_json,
              updated_at=excluded.updated_at
            """,
            (
                state.source,
                state.last_attempt_at.isoformat(),
                None if state.last_success_at is None else state.last_success_at.isoformat(),
                None if state.data_trade_date is None else state.data_trade_date.isoformat(),
                state.status.value,
                state.last_error,
                json.dumps(state.warnings, ensure_ascii=False),
                state.updated_at.isoformat(),
            ),
        )
        if commit:
            self.connection.commit()
        return state

    def get(self, source: str) -> InstrumentCatalogState | None:
        row = self.connection.execute(
            "SELECT * FROM instrument_catalog_state WHERE source=?", (source,)
        ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["warnings"] = json.loads(data.pop("warnings_json", "[]"))
        return InstrumentCatalogState.model_validate(data)


class InstrumentPreviewRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, preview: InstrumentPreview) -> InstrumentPreview:
        preview = InstrumentPreview.model_validate(preview)
        self.connection.execute(
            """
            INSERT INTO instrument_previews (
              preview_id, source, query, items_json, warnings_json,
              created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(preview_id) DO UPDATE SET
              source=excluded.source,
              query=excluded.query,
              items_json=excluded.items_json,
              warnings_json=excluded.warnings_json,
              created_at=excluded.created_at,
              expires_at=excluded.expires_at
            """,
            (
                str(preview.preview_id),
                preview.source.value,
                preview.query,
                json.dumps(
                    [item.model_dump(mode="json") for item in preview.items],
                    ensure_ascii=False,
                ),
                json.dumps(preview.warnings, ensure_ascii=False),
                preview.created_at.isoformat(),
                preview.expires_at.isoformat(),
            ),
        )
        self.connection.commit()
        return preview

    def get(self, preview_id: UUID, *, now: datetime) -> InstrumentPreview:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("preview read time must be timezone-aware")
        row = self.connection.execute(
            "SELECT * FROM instrument_previews WHERE preview_id=?", (str(preview_id),)
        ).fetchone()
        if row is None:
            raise InstrumentPreviewNotFoundError(str(preview_id))
        expires_at = datetime.fromisoformat(row["expires_at"])
        if now >= expires_at:
            self.connection.execute(
                "DELETE FROM instrument_previews WHERE preview_id=?", (str(preview_id),)
            )
            self.connection.commit()
            raise InstrumentPreviewExpiredError(str(preview_id))
        preview = InstrumentPreview.model_validate(
            {
                "preview_id": row["preview_id"],
                "source": row["source"],
                "query": row["query"],
                "items": json.loads(row["items_json"]),
                "warnings": json.loads(row["warnings_json"]),
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            }
        )
        return preview
