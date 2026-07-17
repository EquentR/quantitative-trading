from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from quantitative_trading.notification.models import (
    NotificationCanonicalGroup,
    NotificationStatus,
    NotificationSummary,
    RecommendationNotificationLink,
)


class NotificationRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save(self, summary: NotificationSummary, *, commit: bool = True) -> NotificationSummary:
        self.connection.execute(
            """
            INSERT INTO notifications (
              notification_id,
              dedup_key,
              recommendation_id,
              symbol,
              action,
              status,
              data_time,
              created_at,
              payload_json
            ) VALUES (
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?,
              ?
            )
            ON CONFLICT(notification_id) DO UPDATE SET
              dedup_key = excluded.dedup_key,
              recommendation_id = excluded.recommendation_id,
              symbol = excluded.symbol,
              action = excluded.action,
              status = excluded.status,
              data_time = excluded.data_time,
              created_at = excluded.created_at,
              payload_json = excluded.payload_json
            """,
            (
                summary.notification_id,
                summary.dedup_key,
                summary.recommendation_id,
                summary.symbol,
                summary.action,
                summary.status.value,
                summary.data_time.astimezone(UTC).isoformat(),
                summary.created_at.astimezone(UTC).isoformat(),
                summary.model_dump_json(),
            ),
        )
        if commit:
            self.connection.commit()
        return summary

    def get_by_dedup_key(self, dedup_key: str) -> NotificationSummary | None:
        row = self.connection.execute(
            "SELECT payload_json FROM notifications WHERE dedup_key = ?",
            (dedup_key,),
        ).fetchone()
        if row is None:
            return None
        return NotificationSummary.model_validate_json(row["payload_json"])

    def get(self, notification_id: str) -> NotificationSummary | None:
        row = self.connection.execute(
            """
            SELECT payload_json
            FROM notifications
            WHERE notification_id = ?
            """,
            (notification_id,),
        ).fetchone()
        if row is None:
            return None
        return NotificationSummary.model_validate_json(row["payload_json"])

    def save_canonical_group(
        self,
        canonical_key: str,
        notification_id: str,
        *,
        created_at: datetime,
        commit: bool = True,
    ) -> None:
        group = NotificationCanonicalGroup(
            canonical_key=canonical_key,
            notification_id=notification_id,
            created_at=created_at,
        )
        self.connection.execute(
            """
            INSERT INTO notification_canonical_groups (
              canonical_key, notification_id, created_at
            ) VALUES (?, ?, ?)
            ON CONFLICT(canonical_key) DO NOTHING
            """,
            (
                group.canonical_key,
                group.notification_id,
                group.created_at.astimezone(UTC).isoformat(),
            ),
        )
        row = self.connection.execute(
            """
            SELECT notification_id
            FROM notification_canonical_groups
            WHERE canonical_key = ?
            """,
            (group.canonical_key,),
        ).fetchone()
        if row is None or row["notification_id"] != group.notification_id:
            raise sqlite3.IntegrityError("canonical notification group conflicts")
        if commit:
            self.connection.commit()

    def get_canonical_group(
        self,
        canonical_key: str,
    ) -> NotificationCanonicalGroup | None:
        row = self.connection.execute(
            """
            SELECT canonical_key, notification_id, created_at
            FROM notification_canonical_groups
            WHERE canonical_key = ?
            """,
            (canonical_key,),
        ).fetchone()
        if row is None:
            return None
        return NotificationCanonicalGroup.model_validate(dict(row))

    def link_recommendation(
        self,
        recommendation_id: str,
        notification_id: str,
        canonical_key: str,
        *,
        created_at: datetime,
        commit: bool = True,
    ) -> RecommendationNotificationLink:
        link = RecommendationNotificationLink(
            recommendation_id=recommendation_id,
            notification_id=notification_id,
            canonical_key=canonical_key,
            created_at=created_at,
        )
        self.connection.execute(
            """
            INSERT INTO recommendation_notification_links (
              recommendation_id, notification_id, canonical_key, created_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(recommendation_id) DO NOTHING
            """,
            (
                link.recommendation_id,
                link.notification_id,
                link.canonical_key,
                link.created_at.astimezone(UTC).isoformat(),
            ),
        )
        stored = self.get_link(recommendation_id)
        if stored is None or (
            stored.recommendation_id,
            stored.notification_id,
            stored.canonical_key,
        ) != (
            link.recommendation_id,
            link.notification_id,
            link.canonical_key,
        ):
            raise sqlite3.IntegrityError("recommendation notification link conflicts")
        if commit:
            self.connection.commit()
        return stored

    def get_link(
        self,
        recommendation_id: str,
    ) -> RecommendationNotificationLink | None:
        row = self.connection.execute(
            """
            SELECT recommendation_id, notification_id, canonical_key, created_at
            FROM recommendation_notification_links
            WHERE recommendation_id = ?
            """,
            (recommendation_id,),
        ).fetchone()
        if row is None:
            return None
        return RecommendationNotificationLink.model_validate(dict(row))

    def list_recent(self, *, limit: int = 50) -> list[NotificationSummary]:
        return self.list(limit=limit)

    def list_current(
        self,
        *,
        status: NotificationStatus | None = None,
        symbol: str | None = None,
        action: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationSummary]:
        clauses, parameters = self._current_filters(
            status=status,
            symbol=symbol,
            action=action,
            recommendation_id=recommendation_id,
        )
        parameters.extend((limit, offset))
        rows = self.connection.execute(
            f"""
            SELECT notifications.payload_json
            FROM notifications
            WHERE {' AND '.join(clauses)}
            ORDER BY notifications.created_at DESC, notifications.notification_id DESC
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()
        return [NotificationSummary.model_validate_json(row["payload_json"]) for row in rows]

    def list(
        self,
        *,
        status: NotificationStatus | None = None,
        symbol: str | None = None,
        action: str | None = None,
        recommendation_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationSummary]:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("status", status.value if status is not None else None),
            ("symbol", symbol),
            ("action", action),
            ("recommendation_id", recommendation_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((limit, offset))
        rows = self.connection.execute(
            f"""
            SELECT payload_json
            FROM notifications
            {where}
            ORDER BY created_at DESC, notification_id DESC
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()
        return [NotificationSummary.model_validate_json(row["payload_json"]) for row in rows]

    def count_unread(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM notifications WHERE status = ?",
            (NotificationStatus.UNREAD.value,),
        ).fetchone()
        return int(row["count"])

    def count_current(
        self,
        *,
        status: NotificationStatus | None = None,
        symbol: str | None = None,
        action: str | None = None,
        recommendation_id: str | None = None,
    ) -> int:
        clauses, parameters = self._current_filters(
            status=status,
            symbol=symbol,
            action=action,
            recommendation_id=recommendation_id,
        )
        row = self.connection.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM notifications
            WHERE {' AND '.join(clauses)}
            """,
            parameters,
        ).fetchone()
        return int(row["count"])

    def count(
        self,
        *,
        status: NotificationStatus | None = None,
        symbol: str | None = None,
        action: str | None = None,
        recommendation_id: str | None = None,
    ) -> int:
        clauses: list[str] = []
        parameters: list[object] = []
        for column, value in (
            ("status", status.value if status is not None else None),
            ("symbol", symbol),
            ("action", action),
            ("recommendation_id", recommendation_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM notifications {where}",
            parameters,
        ).fetchone()
        return int(row["count"])

    def list_by_recommendation_id(
        self,
        recommendation_id: str,
        *,
        limit: int | None = None,
    ) -> list[NotificationSummary]:
        if limit is None:
            rows = self.connection.execute(
                """
                SELECT payload_json
                FROM notifications
                WHERE recommendation_id = ?
                ORDER BY created_at DESC, rowid DESC
                """,
                (recommendation_id,),
            ).fetchall()
        else:
            rows = self.connection.execute(
                """
                SELECT payload_json
                FROM notifications
                WHERE recommendation_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (recommendation_id, limit),
            ).fetchall()
        return [NotificationSummary.model_validate_json(row["payload_json"]) for row in rows]

    @staticmethod
    def _current_filters(
        *,
        status: NotificationStatus | None,
        symbol: str | None,
        action: str | None,
        recommendation_id: str | None,
    ) -> tuple[list[str], list[object]]:
        clauses = [
            """
            (
              notifications.action = 'system_alert'
              OR EXISTS (
                SELECT 1
                FROM notification_canonical_groups
                WHERE notification_canonical_groups.notification_id = notifications.notification_id
              )
            )
            """
        ]
        parameters: list[object] = []
        for column, value in (
            ("status", status.value if status is not None else None),
            ("symbol", symbol),
            ("action", action),
            ("recommendation_id", recommendation_id),
        ):
            if value is not None:
                clauses.append(f"notifications.{column} = ?")
                parameters.append(value)
        return clauses, parameters
