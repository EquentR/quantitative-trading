from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.config import Settings
from quantitative_trading.notification.models import NotificationStatus, NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 13, 2, 30, tzinfo=UTC)


def authenticated_client(tmp_path) -> tuple[TestClient, dict[str, str], Settings]:
    settings = Settings(database_path=tmp_path / "api-notifications.db")
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    return (
        client,
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        settings,
    )


def notification(
    notification_id: str,
    *,
    status: NotificationStatus = NotificationStatus.UNREAD,
    symbol: str = "600000",
    recommendation_id: str = "rec-1",
    created_at: datetime = NOW,
) -> NotificationSummary:
    return NotificationSummary(
        notification_id=notification_id,
        recommendation_id=recommendation_id,
        symbol=symbol,
        action="watch",
        confidence="medium",
        key_price=10.5,
        reason=["rule matched"],
        risk=["invalidate below support"],
        data_time=created_at,
        audit_id=f"audit-{notification_id}",
        status=status,
        created_at=created_at,
    )


def audit(audit_id: str, *, event_type: str, created_at: datetime) -> AuditLog:
    return AuditLog(
        audit_id=audit_id,
        event_type=event_type,
        recommendation_id="rec-1",
        payload={"notification_id": "notif-1"},
        created_at=created_at,
    )


def test_notifications_api_supports_auth_pagination_filters_and_unread_count(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        migrate(connection)
        repository = NotificationRepository(connection)
        repository.save(notification("notif-1", created_at=NOW))
        repository.save(notification("notif-2", created_at=NOW + timedelta(minutes=1)))
        repository.save(
            notification(
                "notif-3",
                status=NotificationStatus.READ,
                symbol="000001",
                recommendation_id="rec-2",
                created_at=NOW + timedelta(minutes=2),
            )
        )

    unauthorized = client.get("/api/v1/notifications")
    response = client.get(
        "/api/v1/notifications",
        params={
            "status": "unread",
            "symbol": "600000",
            "recommendation_id": "rec-1",
            "page": 2,
            "page_size": 1,
        },
        headers=headers,
    )
    unread = client.get("/api/v1/notifications/unread-count", headers=headers)

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert [item["notification_id"] for item in response.json()["items"]] == ["notif-1"]
    assert response.json() | {"items": []} == {
        "items": [],
        "total": 2,
        "page": 2,
        "page_size": 1,
    }
    assert unread.status_code == 200
    assert unread.json() == {"count": 2}


def test_mark_notification_read_is_idempotent_preserves_feedback_and_writes_audit(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        migrate(connection)
        repository = NotificationRepository(connection)
        repository.save(notification("notif-unread"))
        repository.save(
            notification("notif-feedback", status=NotificationStatus.FEEDBACK_RECORDED)
        )

    marked = client.post("/api/v1/notifications/notif-unread/read", headers=headers)
    marked_again = client.post("/api/v1/notifications/notif-unread/read", headers=headers)
    feedback = client.post("/api/v1/notifications/notif-feedback/read", headers=headers)

    assert marked.status_code == 200
    assert marked.json()["status"] == "read"
    assert marked_again.json()["status"] == "read"
    assert feedback.json()["status"] == "feedback_recorded"
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=20)
    read_audits = [item for item in audits if item.event_type == "notification.read"]
    assert {item.payload["notification_id"] for item in read_audits} == {
        "notif-unread",
        "notif-feedback",
    }


def test_notification_read_and_audit_detail_return_stable_not_found_errors(tmp_path) -> None:
    client, headers, _settings = authenticated_client(tmp_path)

    notification_response = client.post(
        "/api/v1/notifications/missing/read", headers=headers
    )
    audit_response = client.get("/api/v1/audit/missing", headers=headers)

    assert notification_response.status_code == 404
    assert notification_response.json()["error"]["code"] == "notification_not_found"
    assert audit_response.status_code == 404
    assert audit_response.json()["error"]["code"] == "audit_not_found"


def test_audit_api_supports_stable_pagination_and_filters(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        migrate(connection)
        repository = AuditLogRepository(connection)
        repository.save(audit("audit-1", event_type="notification.created", created_at=NOW))
        repository.save(
            audit(
                "audit-2",
                event_type="notification.created",
                created_at=NOW + timedelta(minutes=1),
            )
        )
        repository.save(
            audit(
                "audit-3",
                event_type="smtp.settings.updated",
                created_at=NOW + timedelta(minutes=2),
            )
        )

    response = client.get(
        "/api/v1/audit",
        params={
            "event_type": "notification.created",
            "recommendation_id": "rec-1",
            "page": 2,
            "page_size": 1,
        },
        headers=headers,
    )
    detail = client.get("/api/v1/audit/audit-2", headers=headers)

    assert response.status_code == 200
    assert [item["audit_id"] for item in response.json()["items"]] == ["audit-1"]
    assert response.json() | {"items": []} == {
        "items": [],
        "total": 2,
        "page": 2,
        "page_size": 1,
    }
    assert detail.status_code == 200
    assert detail.json()["audit_id"] == "audit-2"
