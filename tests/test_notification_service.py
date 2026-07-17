from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
import hashlib
import json
import sqlite3

import pytest
from pydantic import ValidationError

import quantitative_trading.notification.migration as notification_migration
from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.config import Settings
from quantitative_trading.notification.jsonl import JsonlNotificationWriter
from quantitative_trading.notification.models import NotificationStatus, NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.notification.service import NotificationService
from quantitative_trading.recommendation.identity import (
    recommendation_condition_fingerprint,
)
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 9, 2, 30, tzinfo=UTC)
LATER = datetime(2026, 7, 9, 2, 45, tzinfo=UTC)


@pytest.fixture
def service(tmp_path) -> Iterator[NotificationService]:
    settings = Settings(database_path=tmp_path / "notifications.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = NotificationRepository(connection)
        yield NotificationService(repository, id_factory=lambda: "notif-1")


def recommendation(**overrides: object) -> Recommendation:
    data: dict[str, object] = {
        "recommendation_id": "rec-1",
        "symbol": "600000",
        "name": "浦发银行",
        "action": RecommendationAction.WATCH,
        "confidence": "medium",
        "position_context": {"source": "manual_ledger"},
        "account_context": {"source": "manual_cash_account"},
        "price_context": {"current_price": 10.5},
        "reason": ["站上短期均线"],
        "risk": {"invalid_if": ["跌破 10.0"], "notes": ["行情数据可能延迟"]},
        "valid_until": datetime(2026, 7, 9, 7, 0, tzinfo=UTC),
        "data_time": NOW,
    }
    data.update(overrides)
    return Recommendation(**data)


def audit_log(**overrides: object) -> AuditLog:
    data = {
        "audit_id": "audit-1",
        "event_type": "notification.created",
        "recommendation_id": "rec-1",
        "payload": {"channel": "local"},
        "created_at": NOW,
    }
    data.update(overrides)
    return AuditLog(**data)


def test_create_from_recommendation_defaults_to_unread(
    service: NotificationService,
) -> None:
    summary = service.create_from_recommendation(
        recommendation(),
        audit_log(),
        now=NOW,
    )

    assert summary.status is NotificationStatus.UNREAD
    assert summary.notification_id == "notif-1"
    assert summary.recommendation_id == "rec-1"
    assert summary.symbol == "600000"
    assert summary.action == "watch"
    assert summary.confidence == "medium"
    assert summary.key_price == 10.5
    assert summary.reason == ["站上短期均线"]
    assert summary.risk == ["跌破 10.0", "行情数据可能延迟"]
    assert summary.data_time == NOW
    assert summary.audit_id == "audit-1"
    assert service.get("notif-1") == summary


def test_create_from_recommendation_sanitizes_summary_risk(
    service: NotificationService,
) -> None:
    summary = service.create_from_recommendation(
        recommendation(
            risk={
                "invalid_if": ["跌破 10.0"],
                "notes": ["api_key=raw-key token=raw-token cookie=raw-cookie"],
            },
        ),
        audit_log(),
        now=NOW,
    )

    text = summary.model_dump_json().lower()
    assert "api_key" not in text
    assert "token" not in text
    assert "cookie" not in text
    assert "raw-key" not in text
    assert "raw-token" not in text
    assert "raw-cookie" not in text


def test_create_from_recommendation_preserves_long_non_secret_risk(
    service: NotificationService,
) -> None:
    long_risk = "risk-context-" + ("volume-confirmed-" * 30)

    summary = service.create_from_recommendation(
        recommendation(risk={"invalid_if": ["跌破 10.0"], "notes": [long_risk]}),
        audit_log(),
        now=NOW,
    )

    assert len(long_risk) > 300
    assert summary.risk == ["跌破 10.0", long_risk]
    assert service.get("notif-1").risk == ["跌破 10.0", long_risk]


def test_mark_read_changes_only_status(service: NotificationService) -> None:
    original = service.create_from_recommendation(recommendation(), audit_log(), now=NOW)

    updated = service.mark_read("notif-1", now=LATER)

    original_data = original.model_dump(mode="json")
    updated_data = updated.model_dump(mode="json")
    original_data["status"] = "read"
    assert updated_data == original_data
    assert updated.status is NotificationStatus.READ


def test_feedback_changes_status_to_feedback_recorded(
    service: NotificationService,
) -> None:
    service.create_from_recommendation(recommendation(), audit_log(), now=NOW)

    updated = service.mark_feedback_recorded("notif-1", now=LATER)

    assert updated.status is NotificationStatus.FEEDBACK_RECORDED
    assert service.get("notif-1") == updated


def test_notification_current_view_unions_canonical_groups_and_system_alerts(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "notification-current-view.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = NotificationRepository(connection)
        notifications = [
            NotificationSummary(
                notification_id="notif-history-only",
                dedup_key="history-only",
                recommendation_id="rec-history-only",
                symbol="600000",
                action="hold",
                confidence="medium",
                key_price=10.5,
                reason=["history"],
                risk=["review"],
                data_time=NOW,
                audit_id="audit-history-only",
                status=NotificationStatus.UNREAD,
                created_at=NOW,
            ),
            NotificationSummary(
                notification_id="notif-canonical-read",
                dedup_key="canonical-read",
                recommendation_id="rec-canonical-read",
                symbol="600000",
                action="hold",
                confidence="medium",
                key_price=10.5,
                reason=["current read"],
                risk=["review"],
                data_time=NOW + timedelta(minutes=1),
                audit_id="audit-canonical-read",
                status=NotificationStatus.READ,
                created_at=NOW + timedelta(minutes=1),
            ),
            NotificationSummary(
                notification_id="notif-canonical-unread",
                dedup_key="canonical-unread",
                recommendation_id="rec-canonical-unread",
                symbol="600000",
                action="reduce",
                confidence="medium",
                key_price=10.5,
                reason=["current unread"],
                risk=["review"],
                data_time=NOW + timedelta(minutes=2),
                audit_id="audit-canonical-unread",
                status=NotificationStatus.UNREAD,
                created_at=NOW + timedelta(minutes=2),
            ),
            NotificationSummary(
                notification_id="notif-system-alert",
                dedup_key="system-alert",
                recommendation_id="system-alert:provider",
                symbol="000000",
                action="system_alert",
                confidence="critical",
                key_price=None,
                reason=["provider unavailable"],
                risk=["review service state"],
                data_time=NOW + timedelta(minutes=3),
                audit_id="audit-system-alert",
                status=NotificationStatus.UNREAD,
                created_at=NOW + timedelta(minutes=3),
            ),
        ]
        for item in notifications:
            repository.save(item)
        repository.save_canonical_group(
            "canonical-read",
            "notif-canonical-read",
            created_at=NOW + timedelta(minutes=1),
        )
        repository.save_canonical_group(
            "canonical-unread",
            "notif-canonical-unread",
            created_at=NOW + timedelta(minutes=2),
        )
        service = NotificationService(repository)

        history = service.list_notifications()
        current = service.list_notifications(view="current")
        current_unread = service.list_notifications(
            view="current",
            status=NotificationStatus.UNREAD,
        )

    assert [item.notification_id for item in history] == [
        "notif-system-alert",
        "notif-canonical-unread",
        "notif-canonical-read",
        "notif-history-only",
    ]
    assert [item.notification_id for item in current] == [
        "notif-system-alert",
        "notif-canonical-unread",
        "notif-canonical-read",
    ]
    assert [item.notification_id for item in current_unread] == [
        "notif-system-alert",
        "notif-canonical-unread",
    ]


def test_notification_unread_count_uses_current_view_and_symbol_filter(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "notification-current-count.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = NotificationRepository(connection)
        base = NotificationSummary(
            notification_id="notif-history",
            dedup_key="history",
            recommendation_id="rec-history",
            symbol="600000",
            action="hold",
            confidence="medium",
            key_price=10.5,
            reason=["history"],
            risk=["review"],
            data_time=NOW,
            audit_id="audit-history",
            status=NotificationStatus.UNREAD,
            created_at=NOW,
        )
        repository.save(base)
        repository.save(
            base.model_copy(
                update={
                    "notification_id": "notif-current",
                    "dedup_key": "current",
                    "recommendation_id": "rec-current",
                    "audit_id": "audit-current",
                    "created_at": NOW + timedelta(minutes=1),
                }
            )
        )
        repository.save(
            base.model_copy(
                update={
                    "notification_id": "notif-alert",
                    "dedup_key": "alert",
                    "recommendation_id": "system-alert:provider",
                    "symbol": "000000",
                    "action": "system_alert",
                    "audit_id": "audit-alert",
                    "created_at": NOW + timedelta(minutes=2),
                }
            )
        )
        repository.save_canonical_group(
            "current",
            "notif-current",
            created_at=NOW + timedelta(minutes=1),
        )
        service = NotificationService(repository)

        current_count = service.unread_count()
        symbol_count = service.unread_count(symbol="600000")

    assert current_count == 2
    assert symbol_count == 1


def test_notification_condition_key_deduplicates_only_identical_conditions(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "notification-dedup.db")
    ids = iter(("notif-1", "notif-2", "notif-3"))
    with connect(settings) as connection:
        migrate(connection)
        repository = NotificationRepository(connection)
        service = NotificationService(repository, id_factory=lambda: next(ids))

        first = service.create_from_recommendation(
            recommendation(),
            audit_log(),
            dedup_key="2026-07-13:600000:watch:plan-v1:fingerprint-a",
            now=NOW,
        )
        duplicate = service.create_from_recommendation(
            recommendation(recommendation_id="rec-duplicate"),
            audit_log(recommendation_id="rec-duplicate"),
            dedup_key="2026-07-13:600000:watch:plan-v1:fingerprint-a",
            now=LATER,
        )
        changed = service.create_from_recommendation(
            recommendation(recommendation_id="rec-changed"),
            audit_log(recommendation_id="rec-changed"),
            dedup_key="2026-07-13:600000:watch:plan-v1:fingerprint-b",
            now=LATER,
        )

        assert duplicate == first
        assert changed.notification_id == "notif-2"
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 2


def test_recommendation_fingerprint_version_and_notification_link_round_trip(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "notification-link.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            condition_fingerprint="a" * 64,
            condition_fingerprint_version=2,
        )
        saved = RecommendationRepository(connection).save_many(
            [rec],
            created_at=NOW,
        )[0]
        notifications = NotificationRepository(connection)
        summary = NotificationService(
            notifications,
            id_factory=lambda: "notif-link",
        ).create_from_recommendation(rec, audit_log(), now=NOW)

        notifications.save_canonical_group(
            "canonical-key",
            summary.notification_id,
            created_at=NOW,
        )
        link = notifications.link_recommendation(
            rec.recommendation_id,
            summary.notification_id,
            "canonical-key",
            created_at=NOW,
        )

        assert saved.condition_fingerprint_version == 2
        assert connection.execute(
            "SELECT condition_fingerprint_version FROM recommendations"
        ).fetchone()[0] == 2
        assert link.recommendation_id == rec.recommendation_id
        assert link.notification_id == summary.notification_id
        assert notifications.get_link(rec.recommendation_id) == link
        retried_link = notifications.link_recommendation(
            rec.recommendation_id,
            summary.notification_id,
            "canonical-key",
            created_at=LATER,
        )
        assert retried_link == link
        assert retried_link.created_at == NOW
        group = notifications.get_canonical_group("canonical-key")
        assert group is not None
        assert group.notification_id == summary.notification_id

        second_rec = recommendation(
            recommendation_id="rec-2",
            condition_fingerprint="b" * 64,
            condition_fingerprint_version=2,
        )
        RecommendationRepository(connection).save_many([second_rec], created_at=NOW)
        second_summary = summary.model_copy(
            update={
                "notification_id": "notif-2",
                "recommendation_id": second_rec.recommendation_id,
            }
        )
        notifications.save(second_summary)
        with pytest.raises(sqlite3.IntegrityError):
            notifications.link_recommendation(
                second_rec.recommendation_id,
                second_summary.notification_id,
                "canonical-key",
                created_at=NOW,
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "DELETE FROM notifications WHERE notification_id = ?",
                (summary.notification_id,),
            )

        with pytest.raises(ValidationError):
            notifications.save_canonical_group(
                "",
                summary.notification_id,
                created_at=NOW,
            )
        with pytest.raises(ValidationError, match="timezone-aware"):
            notifications.save_canonical_group(
                "naive-time-key",
                summary.notification_id,
                created_at=datetime(2026, 7, 9, 2, 30),
            )


def test_legacy_notification_migration_recomputes_v2_and_preserves_handled_state(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "legacy-notifications.db")
    statuses = (
        NotificationStatus.UNREAD,
        NotificationStatus.READ,
        NotificationStatus.FEEDBACK_RECORDED,
        NotificationStatus.UNREAD,
    )
    with connect(settings) as connection:
        migrate(connection)
        recommendations = [
            recommendation(
                recommendation_id=f"rec-legacy-{index}",
                plan_id="plan-20260713-v1",
                plan_version=1,
                decision_trade_date="2026-07-13",
                condition_fingerprint=str(index + 1) * 64,
                condition_fingerprint_version=1,
                created_at=NOW + timedelta(minutes=index * 3),
            )
            for index in range(3)
        ]
        recommendations.append(
            recommendation(
                recommendation_id="rec-legacy-3",
                plan_id="plan-20260713-v1",
                plan_version=1,
                decision_trade_date="2026-07-13",
                condition_fingerprint="4" * 64,
                condition_fingerprint_version=1,
                risk={"invalid_if": ["跌破 9.8"], "notes": ["行情数据可能延迟"]},
                created_at=NOW + timedelta(minutes=9),
            )
        )
        RecommendationRepository(connection).save_many(
            recommendations,
            created_at=NOW,
        )
        notification_repository = NotificationRepository(connection)
        for index, (rec, status) in enumerate(zip(recommendations, statuses, strict=True)):
            notification_repository.save(
                NotificationSummary(
                    notification_id=f"notif-legacy-{index}",
                    dedup_key=f"legacy-cycle-{index}",
                    recommendation_id=rec.recommendation_id,
                    symbol=rec.symbol,
                    action=rec.action.value,
                    confidence=rec.confidence,
                    key_price=10.5,
                    reason=list(rec.reason),
                    risk=list(rec.risk["invalid_if"]),
                    data_time=rec.data_time,
                    audit_id=f"audit-legacy-{index}",
                    status=status,
                    created_at=NOW + timedelta(minutes=index * 3),
                )
            )

        identity_before = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, dedup_key, condition_fingerprint,
                       condition_fingerprint_version, payload_json
                FROM recommendations
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]
        migrate(connection)
        first_groups = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT canonical_key, notification_id, created_at
                FROM notification_canonical_groups
                ORDER BY canonical_key
                """
            ).fetchall()
        ]
        first_links = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, notification_id, canonical_key, created_at
                FROM recommendation_notification_links
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]
        first_notifications = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT notification_id, dedup_key, status, payload_json
                FROM notifications
                ORDER BY notification_id
                """
            ).fetchall()
        ]
        identity_after_first = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, dedup_key, condition_fingerprint,
                       condition_fingerprint_version, payload_json
                FROM recommendations
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]
        migrate(connection)

        second_groups = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT canonical_key, notification_id, created_at
                FROM notification_canonical_groups
                ORDER BY canonical_key
                """
            ).fetchall()
        ]
        second_links = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, notification_id, canonical_key, created_at
                FROM recommendation_notification_links
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]
        second_notifications = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT notification_id, dedup_key, status, payload_json
                FROM notifications
                ORDER BY notification_id
                """
            ).fetchall()
        ]
        identity_after_second = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, dedup_key, condition_fingerprint,
                       condition_fingerprint_version, payload_json
                FROM recommendations
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]

    assert identity_before == identity_after_first == identity_after_second
    assert len(first_groups) == 2
    assert {row[1] for row in first_groups} == {"notif-legacy-2", "notif-legacy-3"}
    assert {row[0].rsplit(":", 1)[-1] for row in first_groups} == {
        recommendation_condition_fingerprint(recommendations[0]),
        recommendation_condition_fingerprint(recommendations[3]),
    }
    assert [row[1] for row in first_links] == [
        "notif-legacy-2",
        "notif-legacy-2",
        "notif-legacy-2",
        "notif-legacy-3",
    ]
    assert first_groups == second_groups
    assert first_links == second_links
    assert first_notifications == second_notifications


def test_legacy_notification_migration_rejects_inconsistent_notification_payload(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "inconsistent-notification.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            recommendation_id="rec-inconsistent",
            condition_fingerprint="1" * 64,
            condition_fingerprint_version=1,
            decision_trade_date="2026-07-13",
        )
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        stored = NotificationSummary(
            notification_id="notif-inconsistent",
            dedup_key="legacy-inconsistent",
            recommendation_id=rec.recommendation_id,
            symbol=rec.symbol,
            action=rec.action.value,
            confidence=rec.confidence,
            key_price=10.5,
            reason=list(rec.reason),
            risk=list(rec.risk["invalid_if"]),
            data_time=rec.data_time,
            audit_id="audit-inconsistent",
            status=NotificationStatus.UNREAD,
            created_at=NOW,
        )
        NotificationRepository(connection).save(stored)
        inconsistent_payload = stored.model_copy(
            update={"symbol": "000001", "action": "system_alert"}
        ).model_dump_json()
        connection.execute(
            "UPDATE notifications SET payload_json = ? WHERE notification_id = ?",
            (inconsistent_payload, stored.notification_id),
        )
        connection.commit()
        before = tuple(
            connection.execute(
                """
                SELECT symbol, action, status, dedup_key, payload_json
                FROM notifications
                WHERE notification_id = ?
                """,
                (stored.notification_id,),
            ).fetchone()
        )

        migrate(connection)

        after = tuple(
            connection.execute(
                """
                SELECT symbol, action, status, dedup_key, payload_json
                FROM notifications
                WHERE notification_id = ?
                """,
                (stored.notification_id,),
            ).fetchone()
        )
        canonical_keys = [
            row["canonical_key"]
            for row in connection.execute(
                "SELECT canonical_key FROM notification_canonical_groups"
            ).fetchall()
        ]

    assert after == before
    assert not any(key.startswith("notification-v2:") for key in canonical_keys)


def test_legacy_notification_migration_isolates_invalid_records_with_safe_warnings(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "invalid-legacy-notifications.db")
    with connect(settings) as connection:
        migrate(connection)
        invalid_recommendations = [
            recommendation(
                recommendation_id="rec-malformed",
                condition_fingerprint="1" * 64,
                condition_fingerprint_version=1,
            ),
            recommendation(
                recommendation_id="rec-null-fingerprint",
                condition_fingerprint=None,
                condition_fingerprint_version=None,
            ),
            recommendation(
                recommendation_id="rec-bad-fingerprint",
                condition_fingerprint="3" * 64,
                condition_fingerprint_version=1,
            ),
            recommendation(
                recommendation_id="rec-malformed-notification",
                condition_fingerprint="4" * 64,
                condition_fingerprint_version=1,
            ),
        ]
        RecommendationRepository(connection).save_many(
            invalid_recommendations,
            created_at=NOW,
        )
        notification_repository = NotificationRepository(connection)
        notification_recommendations = [
            recommendation(recommendation_id="rec-missing"),
            *invalid_recommendations,
        ]
        for index, rec in enumerate(notification_recommendations):
            notification_repository.save(
                NotificationSummary(
                    notification_id=f"notif-invalid-{index}",
                    dedup_key=f"legacy-invalid-{index}",
                    recommendation_id=rec.recommendation_id,
                    symbol=rec.symbol,
                    action=rec.action.value,
                    confidence=rec.confidence,
                    key_price=10.5,
                    reason=list(rec.reason),
                    risk=list(rec.risk["invalid_if"]),
                    data_time=rec.data_time,
                    audit_id=f"audit-invalid-{index}",
                    status=NotificationStatus.UNREAD,
                    created_at=NOW + timedelta(minutes=index),
                )
            )
        connection.execute(
            """
            UPDATE recommendations
            SET payload_json = ?
            WHERE recommendation_id = 'rec-malformed'
            """,
            ('{"api_key":"super-secret"',),
        )
        bad_fingerprint_payload = invalid_recommendations[2].model_dump(
            mode="json"
        )
        bad_fingerprint_payload["condition_fingerprint"] = "bad"
        connection.execute(
            """
            UPDATE recommendations
            SET condition_fingerprint = 'bad', payload_json = ?
            WHERE recommendation_id = 'rec-bad-fingerprint'
            """,
            (json.dumps(bad_fingerprint_payload),),
        )
        connection.execute(
            """
            UPDATE notifications
            SET payload_json = ?
            WHERE notification_id = 'notif-invalid-4'
            """,
            ('{"token":"super-secret"',),
        )
        connection.commit()
        notifications_before = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT notification_id, symbol, action, status, dedup_key, payload_json
                FROM notifications
                ORDER BY notification_id
                """
            ).fetchall()
        ]
        recommendations_before = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, dedup_key, condition_fingerprint,
                       condition_fingerprint_version, payload_json
                FROM recommendations
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]

        migrate(connection)

        first_notifications = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT notification_id, symbol, action, status, dedup_key, payload_json
                FROM notifications
                ORDER BY notification_id
                """
            ).fetchall()
        ]
        first_recommendations = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, dedup_key, condition_fingerprint,
                       condition_fingerprint_version, payload_json
                FROM recommendations
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]
        first_groups = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT canonical_key, notification_id, created_at
                FROM notification_canonical_groups
                ORDER BY notification_id
                """
            ).fetchall()
        ]
        first_links = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, notification_id, canonical_key, created_at
                FROM recommendation_notification_links
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]
        first_warnings = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT audit_id, recommendation_id, created_at, payload_json
                FROM audit_logs
                WHERE event_type = 'notification.legacy_migration_warning'
                ORDER BY audit_id
                """
            ).fetchall()
        ]

        migrate(connection)

        second_notifications = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT notification_id, symbol, action, status, dedup_key, payload_json
                FROM notifications
                ORDER BY notification_id
                """
            ).fetchall()
        ]
        second_recommendations = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, dedup_key, condition_fingerprint,
                       condition_fingerprint_version, payload_json
                FROM recommendations
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]
        second_groups = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT canonical_key, notification_id, created_at
                FROM notification_canonical_groups
                ORDER BY notification_id
                """
            ).fetchall()
        ]
        second_links = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT recommendation_id, notification_id, canonical_key, created_at
                FROM recommendation_notification_links
                ORDER BY recommendation_id
                """
            ).fetchall()
        ]
        second_warnings = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT audit_id, recommendation_id, created_at, payload_json
                FROM audit_logs
                WHERE event_type = 'notification.legacy_migration_warning'
                ORDER BY audit_id
                """
            ).fetchall()
        ]
        readable_warnings = AuditLogRepository(connection).list(
            event_type="notification.legacy_migration_warning",
            limit=10,
        )

    assert (
        notifications_before == first_notifications == second_notifications
    )
    assert (
        recommendations_before == first_recommendations == second_recommendations
    )
    assert first_groups == second_groups
    assert first_links == second_links
    assert first_warnings == second_warnings
    assert len(first_groups) == 5
    assert {
        notification_id: canonical_key
        for canonical_key, notification_id, _created_at in first_groups
    } == {
        f"notif-invalid-{index}": (
            "notification-legacy:"
            + hashlib.sha256(f"notif-invalid-{index}".encode()).hexdigest()
        )
        for index in range(5)
    }
    assert {
        recommendation_id: notification_id
        for recommendation_id, notification_id, _canonical_key, _created_at in first_links
    } == {
        "rec-malformed": "notif-invalid-1",
        "rec-null-fingerprint": "notif-invalid-2",
        "rec-bad-fingerprint": "notif-invalid-3",
        "rec-malformed-notification": "notif-invalid-4",
    }
    warning_payloads = [audit.payload for audit in readable_warnings]
    assert len(warning_payloads) == 5
    assert all(audit.recommendation_id is None for audit in readable_warnings)
    assert {
        payload["notification_id"]: payload["reason"]
        for payload in warning_payloads
    } == {
        "notif-invalid-0": "missing_recommendation",
        "notif-invalid-1": "invalid_recommendation_payload",
        "notif-invalid-2": "missing_condition_fingerprint",
        "notif-invalid-3": "invalid_condition_fingerprint",
        "notif-invalid-4": "invalid_notification_payload",
    }
    assert all(set(payload) == {"notification_id", "reason"} for payload in warning_payloads)
    assert "super-secret" not in json.dumps(warning_payloads)


def test_invalid_legacy_notification_preserves_preexisting_canonical_group(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "preexisting-legacy-group.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            recommendation_id="rec-preexisting",
            condition_fingerprint="1" * 64,
            condition_fingerprint_version=1,
        )
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        notification = NotificationSummary(
            notification_id="notif-preexisting",
            dedup_key="existing-canonical-key",
            recommendation_id=rec.recommendation_id,
            symbol=rec.symbol,
            action=rec.action.value,
            confidence=rec.confidence,
            key_price=10.5,
            reason=list(rec.reason),
            risk=list(rec.risk["invalid_if"]),
            data_time=rec.data_time,
            audit_id="audit-preexisting",
            status=NotificationStatus.READ,
            created_at=NOW,
        )
        repository = NotificationRepository(connection)
        repository.save(notification)
        repository.save_canonical_group(
            "existing-canonical-key",
            notification.notification_id,
            created_at=NOW,
        )
        repository.link_recommendation(
            rec.recommendation_id,
            notification.notification_id,
            "existing-canonical-key",
            created_at=NOW,
        )
        connection.execute(
            "UPDATE notifications SET payload_json = ? WHERE notification_id = ?",
            ('{"token":"super-secret"', notification.notification_id),
        )
        connection.commit()
        group_before = tuple(
            connection.execute(
                "SELECT * FROM notification_canonical_groups"
            ).fetchone()
        )
        link_before = tuple(
            connection.execute(
                "SELECT * FROM recommendation_notification_links"
            ).fetchone()
        )

        migrate(connection)

        group_after = tuple(
            connection.execute(
                "SELECT * FROM notification_canonical_groups"
            ).fetchone()
        )
        link_after = tuple(
            connection.execute(
                "SELECT * FROM recommendation_notification_links"
            ).fetchone()
        )
        warnings = AuditLogRepository(connection).list(
            event_type="notification.legacy_migration_warning",
            limit=10,
        )

    assert group_after == group_before
    assert link_after == link_before
    assert len(warnings) == 1
    assert warnings[0].payload == {
        "notification_id": "notif-preexisting",
        "reason": "invalid_notification_payload",
    }


def test_valid_legacy_notification_preserves_preexisting_canonical_group(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "preexisting-valid-group.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            recommendation_id="rec-preexisting-valid",
            condition_fingerprint="1" * 64,
            condition_fingerprint_version=1,
            decision_trade_date="2026-07-13",
        )
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        notification = NotificationSummary(
            notification_id="notif-preexisting-valid",
            dedup_key="other-key",
            recommendation_id=rec.recommendation_id,
            symbol=rec.symbol,
            action=rec.action.value,
            confidence=rec.confidence,
            key_price=10.5,
            reason=list(rec.reason),
            risk=list(rec.risk["invalid_if"]),
            data_time=rec.data_time,
            audit_id="audit-preexisting-valid",
            status=NotificationStatus.READ,
            created_at=NOW,
        )
        repository = NotificationRepository(connection)
        repository.save(notification)
        repository.save_canonical_group(
            "other-key",
            notification.notification_id,
            created_at=NOW,
        )
        repository.link_recommendation(
            rec.recommendation_id,
            notification.notification_id,
            "other-key",
            created_at=NOW,
        )
        before = {
            "notifications": [tuple(row) for row in connection.execute("SELECT * FROM notifications")],
            "groups": [
                tuple(row)
                for row in connection.execute("SELECT * FROM notification_canonical_groups")
            ],
            "links": [
                tuple(row)
                for row in connection.execute("SELECT * FROM recommendation_notification_links")
            ],
        }

        migrate(connection)

        after = {
            "notifications": [tuple(row) for row in connection.execute("SELECT * FROM notifications")],
            "groups": [
                tuple(row)
                for row in connection.execute("SELECT * FROM notification_canonical_groups")
            ],
            "links": [
                tuple(row)
                for row in connection.execute("SELECT * FROM recommendation_notification_links")
            ],
        }
        warnings = AuditLogRepository(connection).list(
            event_type="notification.legacy_migration_warning",
            limit=10,
        )

    assert after == before
    assert warnings == []


def test_valid_legacy_notification_backfills_link_for_preexisting_group(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "preexisting-group-missing-link.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            recommendation_id="rec-missing-link",
            condition_fingerprint="1" * 64,
            condition_fingerprint_version=1,
            decision_trade_date="2026-07-13",
        )
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        notification = NotificationSummary(
            notification_id="notif-missing-link",
            dedup_key="other-key",
            recommendation_id=rec.recommendation_id,
            symbol=rec.symbol,
            action=rec.action.value,
            confidence=rec.confidence,
            key_price=10.5,
            reason=list(rec.reason),
            risk=list(rec.risk["invalid_if"]),
            data_time=rec.data_time,
            audit_id="audit-missing-link",
            status=NotificationStatus.READ,
            created_at=NOW,
        )
        repository = NotificationRepository(connection)
        repository.save(notification)
        repository.save_canonical_group(
            "other-key",
            notification.notification_id,
            created_at=NOW,
        )
        group_before = tuple(
            connection.execute("SELECT * FROM notification_canonical_groups").fetchone()
        )
        notification_before = tuple(
            connection.execute("SELECT * FROM notifications").fetchone()
        )

        migrate(connection)

        group_after = tuple(
            connection.execute("SELECT * FROM notification_canonical_groups").fetchone()
        )
        notification_after = tuple(
            connection.execute("SELECT * FROM notifications").fetchone()
        )
        link = connection.execute(
            """
            SELECT recommendation_id, notification_id, canonical_key
            FROM recommendation_notification_links
            """
        ).fetchone()
        warnings = AuditLogRepository(connection).list(
            event_type="notification.legacy_migration_warning",
            limit=10,
        )

    assert group_after == group_before
    assert notification_after == notification_before
    assert link is not None
    assert tuple(link) == (
        rec.recommendation_id,
        notification.notification_id,
        "other-key",
    )
    assert warnings == []


def test_v2_fingerprint_mismatch_isolated_as_invalid_legacy_notification(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "invalid-v2-fingerprint.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            recommendation_id="rec-invalid-v2",
            condition_fingerprint="a" * 64,
            condition_fingerprint_version=2,
            decision_trade_date="2026-07-13",
        )
        assert rec.condition_fingerprint != recommendation_condition_fingerprint(rec)
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        notification = NotificationSummary(
            notification_id="notif-invalid-v2",
            dedup_key="legacy-invalid-v2",
            recommendation_id=rec.recommendation_id,
            symbol=rec.symbol,
            action=rec.action.value,
            confidence=rec.confidence,
            key_price=10.5,
            reason=list(rec.reason),
            risk=list(rec.risk["invalid_if"]),
            data_time=rec.data_time,
            audit_id="audit-invalid-v2",
            status=NotificationStatus.UNREAD,
            created_at=NOW,
        )
        NotificationRepository(connection).save(notification)
        identity_before = tuple(
            connection.execute(
                "SELECT * FROM recommendations WHERE recommendation_id = ?",
                (rec.recommendation_id,),
            ).fetchone()
        )
        notification_before = tuple(
            connection.execute(
                "SELECT * FROM notifications WHERE notification_id = ?",
                (notification.notification_id,),
            ).fetchone()
        )

        migrate(connection)

        identity_after = tuple(
            connection.execute(
                "SELECT * FROM recommendations WHERE recommendation_id = ?",
                (rec.recommendation_id,),
            ).fetchone()
        )
        notification_after = tuple(
            connection.execute(
                "SELECT * FROM notifications WHERE notification_id = ?",
                (notification.notification_id,),
            ).fetchone()
        )
        group = connection.execute(
            "SELECT canonical_key, notification_id FROM notification_canonical_groups"
        ).fetchone()
        link = connection.execute(
            """
            SELECT recommendation_id, notification_id
            FROM recommendation_notification_links
            """
        ).fetchone()
        warnings = AuditLogRepository(connection).list(
            event_type="notification.legacy_migration_warning",
            limit=10,
        )

    assert identity_after == identity_before
    assert notification_after == notification_before
    assert tuple(group) == (
        "notification-legacy:"
        + hashlib.sha256(notification.notification_id.encode()).hexdigest(),
        notification.notification_id,
    )
    assert tuple(link) == (rec.recommendation_id, notification.notification_id)
    assert len(warnings) == 1
    assert warnings[0].payload == {
        "notification_id": notification.notification_id,
        "reason": "invalid_condition_fingerprint",
    }


def test_unsupported_fingerprint_version_isolated_as_invalid_legacy_notification(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "unsupported-fingerprint-version.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            recommendation_id="rec-unsupported-fingerprint",
            condition_fingerprint="b" * 64,
            condition_fingerprint_version=3,
            decision_trade_date="2026-07-13",
        )
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        notification = NotificationSummary(
            notification_id="notif-unsupported-fingerprint",
            dedup_key="legacy-unsupported-fingerprint",
            recommendation_id=rec.recommendation_id,
            symbol=rec.symbol,
            action=rec.action.value,
            confidence=rec.confidence,
            key_price=10.5,
            reason=list(rec.reason),
            risk=list(rec.risk["invalid_if"]),
            data_time=rec.data_time,
            audit_id="audit-unsupported-fingerprint",
            status=NotificationStatus.UNREAD,
            created_at=NOW,
        )
        NotificationRepository(connection).save(notification)
        notification_before = tuple(
            connection.execute("SELECT * FROM notifications").fetchone()
        )
        recommendation_before = tuple(
            connection.execute("SELECT * FROM recommendations").fetchone()
        )

        migrate(connection)

        notification_after = tuple(
            connection.execute("SELECT * FROM notifications").fetchone()
        )
        recommendation_after = tuple(
            connection.execute("SELECT * FROM recommendations").fetchone()
        )
        group = connection.execute(
            "SELECT canonical_key, notification_id FROM notification_canonical_groups"
        ).fetchone()
        link = connection.execute(
            "SELECT recommendation_id, notification_id FROM recommendation_notification_links"
        ).fetchone()
        warnings = AuditLogRepository(connection).list(
            event_type="notification.legacy_migration_warning",
            limit=10,
        )

    assert notification_after == notification_before
    assert recommendation_after == recommendation_before
    assert tuple(group) == (
        "notification-legacy:"
        + hashlib.sha256(notification.notification_id.encode()).hexdigest(),
        notification.notification_id,
    )
    assert tuple(link) == (rec.recommendation_id, notification.notification_id)
    assert len(warnings) == 1
    assert warnings[0].payload == {
        "notification_id": notification.notification_id,
        "reason": "unsupported_condition_fingerprint_version",
    }


def test_invalid_legacy_notifications_share_at_most_one_recommendation_link(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "duplicate-invalid-links.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            recommendation_id="rec-duplicate-invalid",
            condition_fingerprint=None,
            condition_fingerprint_version=None,
        )
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        repository = NotificationRepository(connection)
        for index in range(2):
            repository.save(
                NotificationSummary(
                    notification_id=f"notif-duplicate-invalid-{index}",
                    dedup_key=f"legacy-duplicate-invalid-{index}",
                    recommendation_id=rec.recommendation_id,
                    symbol=rec.symbol,
                    action=rec.action.value,
                    confidence=rec.confidence,
                    key_price=10.5,
                    reason=list(rec.reason),
                    risk=list(rec.risk["invalid_if"]),
                    data_time=rec.data_time,
                    audit_id=f"audit-duplicate-invalid-{index}",
                    status=NotificationStatus.UNREAD,
                    created_at=NOW + timedelta(minutes=index),
                )
            )

        migrate(connection)

        groups = connection.execute(
            "SELECT canonical_key, notification_id FROM notification_canonical_groups"
        ).fetchall()
        links = connection.execute(
            "SELECT recommendation_id, notification_id FROM recommendation_notification_links"
        ).fetchall()
        warnings = AuditLogRepository(connection).list(
            event_type="notification.legacy_migration_warning",
            limit=10,
        )

    assert len(groups) == 2
    assert len(warnings) == 2
    assert [tuple(row) for row in links] == [
        ("rec-duplicate-invalid", "notif-duplicate-invalid-0")
    ]


def test_legacy_migration_warning_sanitizes_sensitive_identifiers(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "sensitive-legacy-warning.db")
    with connect(settings) as connection:
        migrate(connection)
        notification = NotificationSummary(
            notification_id="notif-token=raw-secret",
            dedup_key="legacy-sensitive",
            recommendation_id="rec-api_key=raw-secret",
            symbol="600000",
            action="watch",
            confidence="medium",
            key_price=None,
            reason=["legacy warning"],
            risk=["manual review"],
            data_time=NOW,
            audit_id="audit-sensitive",
            status=NotificationStatus.UNREAD,
            created_at=NOW,
        )
        NotificationRepository(connection).save(notification)

        migrate(connection)

        raw_warning = tuple(
            connection.execute(
                """
                SELECT audit_id, event_type, recommendation_id, payload_json
                FROM audit_logs
                WHERE event_type = 'notification.legacy_migration_warning'
                """
            ).fetchone()
        )
        warning = AuditLogRepository(connection).list(
            event_type="notification.legacy_migration_warning",
            limit=10,
        )[0]

    assert "raw-secret" not in json.dumps(raw_warning)
    assert warning.recommendation_id is None
    assert warning.payload == {
        "notification_id": "notif-[redacted]",
        "reason": "missing_recommendation",
    }


def test_legacy_notification_migration_rolls_back_injected_partial_writes(
    tmp_path,
    monkeypatch,
) -> None:
    settings = Settings(database_path=tmp_path / "legacy-migration-rollback.db")
    with connect(settings) as connection:
        migrate(connection)
        rec = recommendation(
            recommendation_id="rec-rollback",
            condition_fingerprint="1" * 64,
            condition_fingerprint_version=1,
            decision_trade_date="2026-07-13",
        )
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        notification = NotificationSummary(
            notification_id="notif-rollback",
            dedup_key="legacy-rollback",
            recommendation_id=rec.recommendation_id,
            symbol=rec.symbol,
            action=rec.action.value,
            confidence=rec.confidence,
            key_price=10.5,
            reason=list(rec.reason),
            risk=list(rec.risk["invalid_if"]),
            data_time=rec.data_time,
            audit_id="audit-rollback",
            status=NotificationStatus.UNREAD,
            created_at=NOW,
        )
        NotificationRepository(connection).save(notification)
        before = {
            "notifications": [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM notifications ORDER BY notification_id"
                ).fetchall()
            ],
            "groups": [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM notification_canonical_groups ORDER BY canonical_key"
                ).fetchall()
            ],
            "links": [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM recommendation_notification_links ORDER BY recommendation_id"
                ).fetchall()
            ],
        }
        original_fingerprint = (
            notification_migration.recommendation_condition_fingerprint
        )

        def fail_after_partial_writes(recommendation: Recommendation) -> str:
            fingerprint = original_fingerprint(recommendation)
            connection.execute(
                "UPDATE notifications SET dedup_key = 'partial-dedup'"
            )
            connection.execute(
                """
                INSERT INTO notification_canonical_groups (
                  canonical_key, notification_id, created_at
                ) VALUES ('partial-canonical', 'notif-rollback', ?)
                """,
                (NOW.isoformat(),),
            )
            connection.execute(
                """
                INSERT INTO recommendation_notification_links (
                  recommendation_id, notification_id, canonical_key, created_at
                ) VALUES (
                  'rec-rollback', 'notif-rollback', 'partial-canonical', ?
                )
                """,
                (NOW.isoformat(),),
            )
            raise RuntimeError(f"injected migration failure after {fingerprint[:8]}")

        monkeypatch.setattr(
            notification_migration,
            "recommendation_condition_fingerprint",
            fail_after_partial_writes,
        )

        with pytest.raises(RuntimeError, match="injected migration failure"):
            migrate(connection)

        after = {
            "notifications": [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM notifications ORDER BY notification_id"
                ).fetchall()
            ],
            "groups": [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM notification_canonical_groups ORDER BY canonical_key"
                ).fetchall()
            ],
            "links": [
                tuple(row)
                for row in connection.execute(
                    "SELECT * FROM recommendation_notification_links ORDER BY recommendation_id"
                ).fetchall()
            ],
        }

    assert after == before


def test_jsonl_writer_sanitizes_sensitive_keys_and_configured_secret_text(tmp_path) -> None:
    settings = Settings(
        log_dir=tmp_path / "logs",
        api_access_password="local-password-value",
        api_token_secret="configured-secret-text",
    )
    writer = JsonlNotificationWriter(settings)
    rec = recommendation(
        account_context={
            "source": "manual_cash_account",
            "api_key": "raw-key",
            "nested": {"token": "raw-token", "safe": "kept"},
        },
        price_context={"current_price": 10.5, "cookie": "raw-cookie"},
        risk={
            "invalid_if": ["跌破 10.0"],
            "notes": [
                "remote failed with api_key=raw-key token=raw-token cookie=raw-cookie",
                "configured-secret-text",
            ],
        },
    )
    summary = NotificationService(
        NotificationRepository.__new__(NotificationRepository),
        id_factory=lambda: "notif-1",
    ).build_summary(rec, audit_log(), now=NOW)

    writer.write(summary, rec, audit_log())

    log_text = (settings.log_dir / "notifications.jsonl").read_text(encoding="utf-8")
    record = json.loads(log_text)
    lowered = log_text.lower()
    assert record["summary"]["notification_id"] == "notif-1"
    assert "api_key" not in lowered
    assert "token" not in lowered
    assert "cookie" not in lowered
    assert "configured-secret-text" not in log_text
