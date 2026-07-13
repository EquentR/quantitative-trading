import json
from datetime import UTC, datetime, timedelta

from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.config import Settings
from quantitative_trading.email.models import (
    EmailDeliveryStatus,
    SmtpSecurity,
    SmtpSettingsUpdate,
)
from quantitative_trading.email.outbox import EmailDeliveryRepository, EmailDeliveryService
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import SmtpSettingsService
from quantitative_trading.notification.models import NotificationSummary
from quantitative_trading.notification.local_alert import LocalAlertDispatcher
from quantitative_trading.notification.jsonl import JsonlNotificationWriter
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.notification.service import NotificationService
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 13, 4, 0, tzinfo=UTC)


class RecordingSender:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    def send(self, settings, *, recipient: str, subject: str, body: str) -> None:  # noqa: ANN001
        self.calls.append(subject)
        if self.error is not None:
            raise self.error


def configure_smtp(connection) -> None:  # noqa: ANN001
    SmtpSettingsService(SmtpSettingsRepository(connection)).update(
        SmtpSettingsUpdate(
            host="smtp.example.test",
            port=587,
            username="robot@example.test",
            password="synthetic-password",
            sender="robot@example.test",
            recipient="owner@example.test",
            security=SmtpSecurity.STARTTLS,
            enabled=True,
        ),
        now=NOW,
    )


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


def enqueue(service: EmailDeliveryService, *, dedup_key: str = "condition-1"):
    return service.enqueue(
        dedup_key=dedup_key,
        notification_id="notif-1",
        recipient="owner@example.test",
        subject="Trade decision",
        body="Review the local decision and risk conditions.",
        payload={"action": "reduce"},
        now=NOW,
    )


def test_outbox_enqueue_is_conditionally_deduplicated_and_sanitized(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "outbox.db")
    with connect(settings) as connection:
        migrate(connection)
        seed_notification(connection)
        service = EmailDeliveryService(
            EmailDeliveryRepository(connection),
            SmtpSettingsRepository(connection),
            RecordingSender(),
            id_factory=lambda: "delivery-1",
        )
        first = service.enqueue(
            dedup_key="same-condition-password=synthetic-password",
            notification_id="notif-1",
            recipient="owner@example.test",
            subject="Reduce position token=synthetic-token",
            body="Failure detail password=synthetic-password /tmp/private.log",
            payload={"safe": "kept", "password": "synthetic-password"},
            now=NOW,
        )
        second = service.enqueue(
            dedup_key="same-condition-password=synthetic-password",
            notification_id="notif-2",
            recipient="owner@example.test",
            subject="Different subject",
            body="Different body",
            payload={},
            now=NOW,
        )

        assert second == first
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 1
        persisted = first.model_dump_json().lower()
        assert "synthetic-token" not in persisted
        assert "synthetic-password" not in persisted
        assert "/tmp/private.log" not in persisted
        assert first.payload == {"safe": "kept"}


def test_outbox_claim_is_atomic_across_connections_and_recovers_expired_lease(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "claim.db")
    with connect(settings) as setup_connection:
        migrate(setup_connection)
        seed_notification(setup_connection)
        service = EmailDeliveryService(
            EmailDeliveryRepository(setup_connection),
            SmtpSettingsRepository(setup_connection),
            RecordingSender(),
            id_factory=lambda: "delivery-1",
        )
        enqueue(service)

    with connect(settings) as first_connection, connect(settings) as second_connection:
        first_repository = EmailDeliveryRepository(first_connection)
        second_repository = EmailDeliveryRepository(second_connection)

        first_claim = first_repository.claim_due(now=NOW, lease_seconds=60, limit=10)
        competing_claim = second_repository.claim_due(
            now=NOW,
            lease_seconds=60,
            limit=10,
        )
        recovered_count = second_repository.recover_expired_leases(
            now=NOW + timedelta(seconds=61)
        )
        recovered_retry = second_repository.get("delivery-1")
        recovered = second_repository.claim_due(
            now=NOW + timedelta(seconds=61),
            lease_seconds=60,
            limit=10,
        )

        assert [item.delivery_id for item in first_claim] == ["delivery-1"]
        assert competing_claim == []
        assert recovered_count == 1
        assert recovered_retry.status is EmailDeliveryStatus.RETRY
        assert recovered_retry.next_attempt_at == NOW + timedelta(seconds=61)
        assert [item.delivery_id for item in recovered] == ["delivery-1"]
        assert recovered[0].status is EmailDeliveryStatus.SENDING


def test_outbox_failure_uses_exact_backoff_then_dead_and_alerts_locally(
    tmp_path,
    caplog,
) -> None:
    settings = Settings(database_path=tmp_path / "retry.db", log_dir=tmp_path / "logs")
    sender = RecordingSender(
        error=RuntimeError(
            "SMTP failed synthetic-password token=synthetic-token /tmp/mail.log"
        )
    )
    with connect(settings) as connection:
        migrate(connection)
        seed_notification(connection)
        configure_smtp(connection)
        repository = EmailDeliveryRepository(connection)
        audit_repository = AuditLogRepository(connection)
        local_alert_dispatcher = LocalAlertDispatcher(
            notification_service=NotificationService(NotificationRepository(connection)),
            audit_service=AuditService(
                audit_repository,
                configured_secret_texts=("synthetic-password",),
            ),
            jsonl_writer=JsonlNotificationWriter(
                settings,
                configured_secret_texts=("synthetic-password",),
            ),
        )
        service = EmailDeliveryService(
            repository,
            SmtpSettingsRepository(connection),
            sender,
            id_factory=lambda: "delivery-1",
            audit_repository=audit_repository,
            dead_delivery_alert=local_alert_dispatcher.dispatch_dead_email,
        )
        enqueue(service)

        current = NOW
        expected_delays = [1, 5, 15, 30, 60]
        for attempt, delay_minutes in enumerate(expected_delays, start=1):
            processed = service.process_due(now=current)
            delivery = repository.get("delivery-1")
            assert [item.delivery_id for item in processed] == ["delivery-1"]
            assert delivery.attempt_count == attempt
            assert delivery.status is EmailDeliveryStatus.RETRY
            assert delivery.next_attempt_at == current + timedelta(minutes=delay_minutes)
            current = delivery.next_attempt_at

        with caplog.at_level("ERROR"):
            service.process_due(now=current)
        delivery = repository.get("delivery-1")

        assert delivery.status is EmailDeliveryStatus.DEAD
        assert delivery.attempt_count == 6
        assert delivery.next_attempt_at is None
        assert delivery.sent_at is None
        error = delivery.last_error.lower()
        assert "synthetic-password" not in error
        assert "synthetic-token" not in error
        assert "/tmp/mail.log" not in error
        assert len(sender.calls) == 6
        dead_audit = next(
            item
            for item in AuditLogRepository(connection).list_recent(limit=20)
            if item.event_type == "email.delivery.dead"
        )
        assert dead_audit.payload["delivery_id"] == "delivery-1"
        assert "synthetic-password" not in dead_audit.model_dump_json()
        notifications = NotificationRepository(connection).list_recent(limit=20)
        assert len(notifications) == 2
        alert = next(item for item in notifications if item.action == "system_alert")
        assert alert.reason == ["Email delivery permanently failed"]
        records = (settings.log_dir / "notifications.jsonl").read_text().splitlines()
        assert len(records) == 1
        record = json.loads(records[0])
        assert record["summary"]["notification_id"] == alert.notification_id
        assert record["system_alert"]["event_type"] == "email.delivery.dead"
        assert "email.delivery.dead" in caplog.text
        local_outputs = "\n".join(records + [caplog.text]).lower()
        assert "synthetic-password" not in local_outputs
        assert "synthetic-token" not in local_outputs
        assert "/tmp/mail.log" not in local_outputs


def test_outbox_success_marks_sent(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "sent.db")
    sender = RecordingSender()
    with connect(settings) as connection:
        migrate(connection)
        seed_notification(connection)
        configure_smtp(connection)
        repository = EmailDeliveryRepository(connection)
        service = EmailDeliveryService(
            repository,
            SmtpSettingsRepository(connection),
            sender,
            id_factory=lambda: "delivery-1",
        )
        enqueue(service)

        processed = service.process_due(now=NOW)
        delivery = repository.get("delivery-1")

        assert [item.status for item in processed] == [EmailDeliveryStatus.SENT]
        assert delivery.status is EmailDeliveryStatus.SENT
        assert delivery.attempt_count == 1
        assert delivery.sent_at == NOW
        assert delivery.last_error == ""
        assert sender.calls == ["Trade decision"]


def test_dead_delivery_remains_dead_when_local_alert_projection_fails(
    tmp_path,
    caplog,
) -> None:
    settings = Settings(database_path=tmp_path / "dead-alert-failure.db")
    sender = RecordingSender(error=RuntimeError("SMTP unavailable synthetic-password"))

    def failing_alert(**kwargs) -> None:  # noqa: ANN003
        del kwargs
        raise OSError("alert path unavailable synthetic-password /tmp/alerts.log")

    with connect(settings) as connection:
        migrate(connection)
        seed_notification(connection)
        configure_smtp(connection)
        repository = EmailDeliveryRepository(connection)
        service = EmailDeliveryService(
            repository,
            SmtpSettingsRepository(connection),
            sender,
            id_factory=lambda: "delivery-1",
            retry_delays_minutes=(),
            audit_repository=AuditLogRepository(connection),
            dead_delivery_alert=failing_alert,
        )
        enqueue(service)

        with caplog.at_level("ERROR"):
            processed = service.process_due(now=NOW)

        assert [item.status for item in processed] == [EmailDeliveryStatus.DEAD]
        assert repository.get("delivery-1").status is EmailDeliveryStatus.DEAD
        assert "email_dead_local_alert_failed" in caplog.text
        assert "synthetic-password" not in caplog.text
        assert "/tmp/alerts.log" not in caplog.text
