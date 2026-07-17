from collections.abc import Iterator
from datetime import UTC, datetime
import json
import sqlite3

import pytest
from pydantic import ValidationError

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.config import Settings
from quantitative_trading.notification.jsonl import JsonlNotificationWriter
from quantitative_trading.notification.models import NotificationStatus
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.notification.service import NotificationService
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
