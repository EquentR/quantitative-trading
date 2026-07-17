from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.notification.identity import notification_canonical_key
from quantitative_trading.notification.models import NotificationStatus, NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.recommendation.identity import (
    CONDITION_FINGERPRINT_VERSION,
    recommendation_condition_fingerprint,
)
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.sanitization import sanitize_sensitive_data


SHANGHAI = ZoneInfo("Asia/Shanghai")
_STATUS_PRIORITY = {
    NotificationStatus.UNREAD: 0,
    NotificationStatus.READ: 1,
    NotificationStatus.FEEDBACK_RECORDED: 2,
}


@dataclass(frozen=True)
class _LegacyCandidate:
    recommendation: Recommendation
    notification: NotificationSummary
    canonical_key: str


@dataclass(frozen=True)
class _InvalidLegacyNotification:
    notification_id: str
    recommendation_id: str
    recommendation_exists: bool
    created_at: datetime
    reason: str


def migrate_legacy_recommendation_notifications(connection: sqlite3.Connection) -> None:
    repository = NotificationRepository(connection)
    grouped: dict[str, list[_LegacyCandidate]] = {}
    invalid: list[_InvalidLegacyNotification] = []
    rows = connection.execute(
        """
        SELECT n.notification_id,
               n.recommendation_id AS notification_recommendation_id,
               n.symbol AS notification_symbol,
               n.action AS notification_action,
               n.status AS notification_status,
               n.data_time AS notification_data_time,
               n.created_at AS notification_created_at,
               n.payload_json AS notification_payload_json,
               r.recommendation_id,
               r.symbol AS recommendation_symbol,
               r.action AS recommendation_action,
               r.condition_fingerprint,
               r.condition_fingerprint_version,
               r.created_at AS recommendation_created_at,
               r.payload_json AS recommendation_payload_json
        FROM notifications AS n
        LEFT JOIN recommendations AS r
          ON r.recommendation_id = n.recommendation_id
        WHERE n.action != 'system_alert'
        ORDER BY n.created_at, n.notification_id
        """
    ).fetchall()
    for row in rows:
        candidate, invalid_reason = _classify_candidate(row)
        if candidate is not None:
            existing_group = connection.execute(
                """
                SELECT canonical_key, notification_id
                FROM notification_canonical_groups
                WHERE notification_id = ?
                """,
                (candidate.notification.notification_id,),
            ).fetchone()
            if existing_group is not None:
                if repository.get_link(candidate.recommendation.recommendation_id) is None:
                    repository.link_recommendation(
                        candidate.recommendation.recommendation_id,
                        existing_group["notification_id"],
                        existing_group["canonical_key"],
                        created_at=candidate.notification.created_at,
                        commit=False,
                    )
                continue
            grouped.setdefault(candidate.canonical_key, []).append(candidate)
        elif invalid_reason is not None:
            invalid.append(_invalid_notification(row, invalid_reason))

    for canonical_key, candidates in grouped.items():
        winner = max(
            candidates,
            key=lambda item: (
                _STATUS_PRIORITY[item.notification.status],
                item.notification.created_at,
                item.notification.notification_id,
            ),
        )
        existing_group = repository.get_canonical_group(canonical_key)
        canonical_notification = (
            repository.get(existing_group.notification_id)
            if existing_group is not None
            else winner.notification
        )
        if canonical_notification is None:
            raise RuntimeError("canonical notification reference is missing")
        if canonical_notification.dedup_key != canonical_key:
            canonical_notification = canonical_notification.model_copy(
                update={"dedup_key": canonical_key}
            )
            repository.save(canonical_notification, commit=False)
        repository.save_canonical_group(
            canonical_key,
            canonical_notification.notification_id,
            created_at=canonical_notification.created_at,
            commit=False,
        )
        for candidate in candidates:
            repository.link_recommendation(
                candidate.recommendation.recommendation_id,
                canonical_notification.notification_id,
                canonical_key,
                created_at=candidate.notification.created_at,
                commit=False,
            )
    for item in invalid:
        _migrate_invalid_notification(connection, repository, item)


def _classify_candidate(
    row: sqlite3.Row,
) -> tuple[_LegacyCandidate | None, str | None]:
    if row["recommendation_id"] is None:
        return None, "missing_recommendation"
    try:
        notification = NotificationSummary.model_validate_json(
            row["notification_payload_json"]
        )
    except (TypeError, ValueError):
        return None, "invalid_notification_payload"
    try:
        raw_recommendation = json.loads(row["recommendation_payload_json"])
    except (TypeError, ValueError):
        return None, "invalid_recommendation_payload"
    if not isinstance(raw_recommendation, dict):
        return None, "invalid_recommendation_payload"
    payload_fingerprint = raw_recommendation.get("condition_fingerprint")
    payload_version = raw_recommendation.get("condition_fingerprint_version")
    if (
        payload_fingerprint is None
        or row["condition_fingerprint"] is None
        or payload_version is None
        or row["condition_fingerprint_version"] is None
    ):
        return None, "missing_condition_fingerprint"
    if (
        not isinstance(payload_fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", payload_fingerprint) is None
        or not isinstance(row["condition_fingerprint"], str)
        or re.fullmatch(r"[0-9a-f]{64}", row["condition_fingerprint"]) is None
        or payload_fingerprint != row["condition_fingerprint"]
        or not isinstance(payload_version, int)
        or payload_version < 1
        or not isinstance(row["condition_fingerprint_version"], int)
        or row["condition_fingerprint_version"] < 1
        or payload_version != row["condition_fingerprint_version"]
    ):
        return None, "invalid_condition_fingerprint"
    if payload_version > CONDITION_FINGERPRINT_VERSION:
        return None, "unsupported_condition_fingerprint_version"
    try:
        recommendation = Recommendation.model_validate(raw_recommendation)
    except (TypeError, ValueError):
        return None, "invalid_recommendation_payload"
    if (
        recommendation.recommendation_id != row["recommendation_id"]
        or recommendation.recommendation_id
        != row["notification_recommendation_id"]
        or recommendation.symbol != row["recommendation_symbol"]
        or recommendation.symbol != row["notification_symbol"]
        or recommendation.action.value != row["recommendation_action"]
        or recommendation.action.value != row["notification_action"]
        or notification.notification_id != row["notification_id"]
        or notification.recommendation_id != recommendation.recommendation_id
        or notification.symbol != row["notification_symbol"]
        or notification.symbol != recommendation.symbol
        or notification.action != row["notification_action"]
        or notification.action != recommendation.action.value
        or notification.status.value != row["notification_status"]
        or notification.created_at.astimezone(UTC).isoformat()
        != row["notification_created_at"]
        or recommendation.condition_fingerprint is None
        or row["condition_fingerprint"] is None
    ):
        return None, "invalid_notification_payload"
    trade_date = _legacy_decision_trade_date(
        recommendation,
        stored_created_at=row["recommendation_created_at"],
    )
    condition_fingerprint = recommendation_condition_fingerprint(recommendation)
    if (
        recommendation.condition_fingerprint_version
        == CONDITION_FINGERPRINT_VERSION
        and recommendation.condition_fingerprint != condition_fingerprint
    ):
        return None, "invalid_condition_fingerprint"
    return (
        _LegacyCandidate(
            recommendation=recommendation,
            notification=notification,
            canonical_key=notification_canonical_key(
                recommendation,
                trade_date=trade_date,
                plan_version=recommendation.plan_version,
                condition_fingerprint=condition_fingerprint,
            ),
        ),
        None,
    )


def _invalid_notification(
    row: sqlite3.Row,
    reason: str,
) -> _InvalidLegacyNotification:
    created_at = _parse_aware_datetime(row["notification_created_at"])
    if created_at is None:
        created_at = _parse_aware_datetime(row["notification_data_time"])
    if created_at is None:
        created_at = datetime(1970, 1, 1, tzinfo=UTC)
    return _InvalidLegacyNotification(
        notification_id=row["notification_id"],
        recommendation_id=row["notification_recommendation_id"],
        recommendation_exists=row["recommendation_id"] is not None,
        created_at=created_at,
        reason=reason,
    )


def _migrate_invalid_notification(
    connection: sqlite3.Connection,
    repository: NotificationRepository,
    item: _InvalidLegacyNotification,
) -> None:
    digest = hashlib.sha256(item.notification_id.encode("utf-8")).hexdigest()
    canonical_key = f"notification-legacy:{digest}"
    existing_group = connection.execute(
        """
        SELECT canonical_key
        FROM notification_canonical_groups
        WHERE notification_id = ?
        """,
        (item.notification_id,),
    ).fetchone()
    if existing_group is None:
        repository.save_canonical_group(
            canonical_key,
            item.notification_id,
            created_at=item.created_at,
            commit=False,
        )
    else:
        canonical_key = existing_group["canonical_key"]
    if item.recommendation_exists and repository.get_link(item.recommendation_id) is None:
        repository.link_recommendation(
            item.recommendation_id,
            item.notification_id,
            canonical_key,
            created_at=item.created_at,
            commit=False,
        )
    audit_digest = hashlib.sha256(
        f"{item.notification_id}:{item.reason}".encode("utf-8")
    ).hexdigest()
    AuditLogRepository(connection).save(
        AuditLog(
            audit_id=f"audit-legacy-notification-{audit_digest}",
            event_type="notification.legacy_migration_warning",
            recommendation_id=None,
            payload=sanitize_sensitive_data(
                {
                    "notification_id": item.notification_id,
                    "reason": item.reason,
                }
            ),
            created_at=item.created_at,
        ),
        commit=False,
    )


def _legacy_decision_trade_date(
    recommendation: Recommendation,
    *,
    stored_created_at: str,
) -> date:
    if recommendation.decision_trade_date is not None:
        return recommendation.decision_trade_date
    cycle = _parse_aware_datetime(recommendation.decision_cycle)
    if cycle is not None:
        return cycle.astimezone(SHANGHAI).date()
    if recommendation.created_at is not None:
        return recommendation.created_at.astimezone(SHANGHAI).date()
    stored = _parse_aware_datetime(stored_created_at)
    if stored is not None:
        return stored.astimezone(SHANGHAI).date()
    return recommendation.data_time.astimezone(SHANGHAI).date()


def _parse_aware_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed
