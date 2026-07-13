from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.config import Settings
from quantitative_trading.email.outbox import EmailDeliveryRepository, EmailDeliveryService
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.notification.models import NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 13, 5, 0, tzinfo=UTC)


class NoopSender:
    def send(self, settings, *, recipient: str, subject: str, body: str) -> None:  # noqa: ANN001
        pass


def seed_notification(connection) -> None:  # noqa: ANN001
    NotificationRepository(connection).save(
        NotificationSummary(
            notification_id="notif-1",
            recommendation_id="rec-1",
            symbol="600000",
            action="reduce",
            confidence="medium",
            key_price=10.5,
            reason=["risk rule"],
            risk=["manual review"],
            data_time=NOW,
            audit_id="audit-1",
            created_at=NOW,
        )
    )


def authenticated_client(tmp_path) -> tuple[TestClient, dict[str, str], Settings]:
    settings = Settings(database_path=tmp_path / "api-deliveries.db")
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    return client, {"Authorization": f"Bearer {login.json()['access_token']}"}, settings


def test_email_delivery_api_lists_filters_details_and_manually_retries_dead(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        migrate(connection)
        seed_notification(connection)
        repository = EmailDeliveryRepository(connection)
        service = EmailDeliveryService(
            repository,
            SmtpSettingsRepository(connection),
            NoopSender(),
            id_factory=lambda: "delivery-1",
        )
        delivery = service.enqueue(
            dedup_key="condition-1",
            notification_id="notif-1",
            recipient="owner@example.test",
            subject="Risk alert",
            body="Review locally.",
            payload={},
            now=NOW,
        )
        connection.execute(
            """
            UPDATE email_deliveries
            SET status = 'dead', attempt_count = 6, next_attempt_at = NULL,
                last_error = 'safe failure'
            WHERE delivery_id = ?
            """,
            (delivery.delivery_id,),
        )
        connection.commit()

    unauthorized = client.get("/api/v1/notifications/email-deliveries")
    listed = client.get(
        "/api/v1/notifications/email-deliveries",
        params={
            "status": "dead",
            "notification_id": "notif-1",
            "page": 1,
            "page_size": 1,
        },
        headers=headers,
    )
    detail = client.get(
        "/api/v1/notifications/email-deliveries/delivery-1", headers=headers
    )
    retried = client.post(
        "/api/v1/notifications/email-deliveries/delivery-1/retry", headers=headers
    )

    assert unauthorized.status_code == 401
    assert [item["delivery_id"] for item in listed.json()["items"]] == ["delivery-1"]
    assert listed.json() | {"items": []} == {
        "items": [],
        "total": 1,
        "page": 1,
        "page_size": 1,
    }
    assert detail.json()["status"] == "dead"
    assert retried.status_code == 200
    assert retried.json()["status"] == "pending"
    assert retried.json()["attempt_count"] == 0
    assert retried.json()["next_attempt_at"] is not None
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=20)
    retry_audit = next(item for item in audits if item.event_type == "email.delivery.retried")
    assert retry_audit.payload == {"delivery_id": "delivery-1"}


def test_email_delivery_api_rejects_missing_and_sent_retry(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        migrate(connection)
        repository = EmailDeliveryRepository(connection)
        service = EmailDeliveryService(
            repository,
            SmtpSettingsRepository(connection),
            NoopSender(),
            id_factory=lambda: "delivery-sent",
        )
        service.enqueue(
            dedup_key="sent-condition",
            notification_id=None,
            recipient="owner@example.test",
            subject="Daily summary",
            body="Summary.",
            payload={},
            now=NOW - timedelta(minutes=1),
        )
        connection.execute(
            "UPDATE email_deliveries SET status = 'sent', sent_at = ? WHERE delivery_id = ?",
            (NOW.isoformat(), "delivery-sent"),
        )
        connection.commit()

    missing = client.get(
        "/api/v1/notifications/email-deliveries/missing", headers=headers
    )
    retry_sent = client.post(
        "/api/v1/notifications/email-deliveries/delivery-sent/retry", headers=headers
    )

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "email_delivery_not_found"
    assert retry_sent.status_code == 409
    assert retry_sent.json()["error"]["code"] == "email_delivery_not_retryable"
