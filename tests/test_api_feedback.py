from datetime import UTC, datetime
import sqlite3

from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.config import Settings
from quantitative_trading.notification.models import NotificationStatus, NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect
from quantitative_trading.storage.sqlite import migrate


NOW = datetime(2026, 7, 9, 10, 30, tzinfo=UTC)


def authenticated_client(tmp_path) -> tuple[TestClient, dict[str, str], Settings]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    token = login.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}, settings


def position_payload(symbol: str = "600000") -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": "浦发银行",
        "quantity": 1000,
        "available_quantity": 800,
        "cost_price": 9.5,
        "opened_at": "2026-07-06",
        "note": "first lot",
    }


def notification_summary(
    *,
    notification_id: str = "notif-1",
    recommendation_id: str = "rec-1",
) -> NotificationSummary:
    return NotificationSummary(
        notification_id=notification_id,
        recommendation_id=recommendation_id,
        symbol="600000",
        action="watch",
        confidence="medium",
        key_price=10.5,
        reason=["站上短期均线"],
        risk=["跌破 10.0"],
        data_time=NOW,
        audit_id="audit-1",
        status=NotificationStatus.UNREAD,
        created_at=NOW,
    )


def test_post_feedback_records_manual_execution_without_mutating_ledgers(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    client.post("/api/v1/positions", json=position_payload(), headers=headers)
    client.post(
        "/api/v1/cash/account",
        json={"cash": 50000, "note": "initial principal"},
        headers=headers,
    )
    with connect(settings) as connection:
        migrate(connection)
        NotificationRepository(connection).save(notification_summary())

    original_position = client.get("/api/v1/positions/600000", headers=headers).json()
    original_cash = client.get("/api/v1/cash/account", headers=headers).json()
    original_transactions = client.get(
        "/api/v1/cash/transactions?limit=20",
        headers=headers,
    ).json()

    response = client.post(
        "/api/v1/feedback",
        json={
            "recommendation_id": "rec-1",
            "executed": True,
            "execution_price": 10.25,
            "execution_quantity": 100,
            "note": "manual execution api_key=raw-key token=raw-token",
        },
        headers=headers,
    )
    position_after = client.get("/api/v1/positions/600000", headers=headers).json()
    cash_after = client.get("/api/v1/cash/account", headers=headers).json()
    transactions_after = client.get(
        "/api/v1/cash/transactions?limit=20",
        headers=headers,
    ).json()
    list_response = client.get("/api/v1/feedback?recommendation_id=rec-1", headers=headers)

    assert response.status_code == 201
    body = response.json()
    assert body["recommendation_id"] == "rec-1"
    assert body["executed"] is True
    assert body["execution_price"] == 10.25
    assert body["execution_quantity"] == 100
    lowered = response.text.lower()
    assert "api_key" not in lowered
    assert "token" not in lowered
    assert "raw-key" not in lowered
    assert "raw-token" not in lowered
    assert position_after == original_position
    assert cash_after == original_cash
    assert transactions_after == original_transactions
    assert list_response.status_code == 200
    assert [item["recommendation_id"] for item in list_response.json()["items"]] == [
        "rec-1"
    ]
    assert list_response.json()["total"] == 1
    with connect(settings) as connection:
        notification = NotificationRepository(connection).get("notif-1")
        audits = AuditLogRepository(connection).list_recent(limit=20)
    assert notification is not None
    assert notification.status is NotificationStatus.FEEDBACK_RECORDED
    feedback_audit = next(item for item in audits if item.event_type == "feedback.recorded")
    assert feedback_audit.recommendation_id == "rec-1"
    assert feedback_audit.payload == {
        "feedback_id": body["feedback_id"],
        "executed": True,
    }


def test_post_feedback_rolls_back_when_notification_update_fails(
    tmp_path,
    monkeypatch,
) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        migrate(connection)
        NotificationRepository(connection).save(notification_summary())

    def fail_save(self, summary, *, commit=True):  # noqa: ANN001, ARG001
        raise sqlite3.OperationalError("simulated notification write failure")

    monkeypatch.setattr(NotificationRepository, "save", fail_save)

    response = client.post(
        "/api/v1/feedback",
        json={"recommendation_id": "rec-1", "executed": False},
        headers=headers,
    )
    list_response = client.get("/api/v1/feedback?recommendation_id=rec-1", headers=headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert list_response.status_code == 200
    assert list_response.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 50,
    }
    with connect(settings) as connection:
        notification = NotificationRepository(connection).get("notif-1")
    assert notification is not None
    assert notification.status is NotificationStatus.UNREAD


def test_post_feedback_marks_all_matching_notifications(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        migrate(connection)
        repository = NotificationRepository(connection)
        for index in range(55):
            repository.save(notification_summary(notification_id=f"notif-{index:02d}"))
        repository.save(
            notification_summary(
                notification_id="notif-same-symbol-different-recommendation",
                recommendation_id="rec-other",
            )
        )

    response = client.post(
        "/api/v1/feedback",
        json={"recommendation_id": "rec-1", "executed": False},
        headers=headers,
    )

    assert response.status_code == 201
    with connect(settings) as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM notifications
            WHERE recommendation_id = ?
            GROUP BY status
            """,
            ("rec-1",),
        ).fetchall()
    assert {row["status"]: row["count"] for row in rows} == {"feedback_recorded": 55}
    with connect(settings) as connection:
        decoy = NotificationRepository(connection).get(
            "notif-same-symbol-different-recommendation"
        )
    assert decoy is not None
    assert decoy.status is NotificationStatus.UNREAD


def test_post_feedback_uses_recommendation_link_before_legacy_exact_match(
    tmp_path,
) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        migrate(connection)
        latest = Recommendation(
            recommendation_id="rec-latest-cycle",
            symbol="600000",
            name="浦发银行",
            action=RecommendationAction.WATCH,
            confidence="medium",
            position_context={},
            account_context={},
            price_context={"current_price": 10.5},
            reason=["same canonical condition"],
            risk={"invalid_if": ["跌破 10.0"]},
            valid_until=NOW.replace(hour=15),
            data_time=NOW,
        )
        RecommendationRepository(connection).save_many([latest], created_at=NOW)
        repository = NotificationRepository(connection)
        canonical = notification_summary(
            notification_id="notif-canonical-original",
            recommendation_id="rec-original-cycle",
        )
        repository.save(canonical)
        repository.save_canonical_group(
            "canonical-feedback-key",
            canonical.notification_id,
            created_at=NOW,
        )
        repository.link_recommendation(
            latest.recommendation_id,
            canonical.notification_id,
            "canonical-feedback-key",
            created_at=NOW,
        )
        repository.save(
            notification_summary(
                notification_id="notif-legacy-decoy",
                recommendation_id=latest.recommendation_id,
            )
        )

    response = client.post(
        "/api/v1/feedback",
        json={"recommendation_id": latest.recommendation_id, "executed": False},
        headers=headers,
    )

    assert response.status_code == 201
    with connect(settings) as connection:
        repository = NotificationRepository(connection)
        canonical_after = repository.get(canonical.notification_id)
        decoy_after = repository.get("notif-legacy-decoy")
    assert canonical_after is not None
    assert canonical_after.status is NotificationStatus.FEEDBACK_RECORDED
    assert decoy_after is not None
    assert decoy_after.status is NotificationStatus.UNREAD


def test_feedback_endpoints_require_authentication_after_setup(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})

    post_response = client.post(
        "/api/v1/feedback",
        json={"recommendation_id": "rec-1", "executed": False},
    )
    get_response = client.get("/api/v1/feedback?recommendation_id=rec-1")

    assert post_response.status_code == 401
    assert post_response.json()["error"]["code"] == "unauthorized"
    assert get_response.status_code == 401
    assert get_response.json()["error"]["code"] == "unauthorized"
