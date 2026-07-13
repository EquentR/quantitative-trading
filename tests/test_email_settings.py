from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient
import pytest

from quantitative_trading.api.app import create_app
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.config import Settings
from quantitative_trading.email.models import SmtpSecurity, SmtpSettingsUpdate
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import SmtplibEmailSender, SmtpSettingsService
import quantitative_trading.email.service as email_service_module
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 13, 3, 0, tzinfo=UTC)
SYNTHETIC_PASSWORD = "synthetic-smtp-password"


class RecordingEmailSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def send(self, settings, *, recipient: str, subject: str, body: str) -> None:  # noqa: ANN001
        self.calls.append((recipient, subject, body))
        if self.error is not None:
            raise self.error


class RecordingConnectionTester:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, int, str, SmtpSecurity]] = []

    def test_connection(self, settings) -> None:  # noqa: ANN001
        self.calls.append(
            (settings.host, settings.port, settings.username, settings.security)
        )
        if self.error is not None:
            raise self.error


@pytest.fixture
def smtp_service(tmp_path) -> Iterator[tuple[SmtpSettingsService, SmtpSettingsRepository]]:
    settings = Settings(database_path=tmp_path / "smtp-settings.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = SmtpSettingsRepository(connection)
        yield SmtpSettingsService(repository), repository


def smtp_update(*, password: str | None = SYNTHETIC_PASSWORD) -> SmtpSettingsUpdate:
    return SmtpSettingsUpdate(
        host="smtp.example.test",
        port=587,
        username="robot@example.test",
        password=password,
        sender="robot@example.test",
        recipient="owner@example.test",
        security=SmtpSecurity.STARTTLS,
        enabled=True,
    )


def authenticated_client(
    tmp_path,
    *,
    sender: RecordingEmailSender | None = None,
    connection_tester: RecordingConnectionTester | None = None,
) -> tuple[TestClient, dict[str, str], Settings]:
    settings = Settings(database_path=tmp_path / "api-email.db")
    client = TestClient(
        create_app(
            settings,
            email_sender=sender,
            smtp_connection_tester=connection_tester,
        )
    )
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    return client, {"Authorization": f"Bearer {login.json()['access_token']}"}, settings


def test_smtp_password_is_plaintext_in_sqlite_but_never_returned(
    smtp_service,
) -> None:
    service, repository = smtp_service

    public = service.update(smtp_update(), now=NOW)
    raw_password = repository.connection.execute(
        "SELECT password FROM smtp_settings WHERE id = 1"
    ).fetchone()["password"]

    assert raw_password == SYNTHETIC_PASSWORD
    assert public.password_configured is True
    assert "password" not in public.model_dump(exclude={"password_configured"})
    assert SYNTHETIC_PASSWORD not in public.model_dump_json()


def test_blank_smtp_password_preserves_existing_and_clear_is_explicit(
    smtp_service,
) -> None:
    service, repository = smtp_service
    service.update(smtp_update(), now=NOW)

    preserved = service.update(smtp_update(password=""), now=NOW)
    assert repository.get().password == SYNTHETIC_PASSWORD
    assert preserved.password_configured is True

    cleared = service.clear_password(now=NOW)
    assert repository.get().password is None
    assert cleared.password_configured is False


def test_smtp_settings_api_is_authenticated_audited_and_never_echoes_password(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    payload = smtp_update().model_dump(mode="json")

    unauthorized = client.get("/api/v1/settings/notifications/email")
    updated = client.put(
        "/api/v1/settings/notifications/email",
        json=payload,
        headers=headers,
    )
    fetched = client.get("/api/v1/settings/notifications/email", headers=headers)
    cleared = client.delete(
        "/api/v1/settings/notifications/email/password", headers=headers
    )

    assert unauthorized.status_code == 401
    assert updated.status_code == 200
    assert fetched.status_code == 200
    assert cleared.status_code == 200
    assert updated.json()["password_configured"] is True
    assert fetched.json()["password_configured"] is True
    assert cleared.json()["password_configured"] is False
    combined = updated.text + fetched.text + cleared.text
    assert SYNTHETIC_PASSWORD not in combined
    assert '"password"' not in combined
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=20)
    audit_text = " ".join(item.model_dump_json() for item in audits)
    assert {item.event_type for item in audits} >= {
        "smtp.settings.updated",
        "smtp.password.cleared",
    }
    assert SYNTHETIC_PASSWORD not in audit_text


def test_smtp_test_api_uses_injected_sender_and_sanitizes_failures(tmp_path) -> None:
    sender = RecordingEmailSender(
        RuntimeError(
            f"login failed password={SYNTHETIC_PASSWORD} token=synthetic-token /tmp/mail.log"
        )
    )
    client, headers, settings = authenticated_client(tmp_path, sender=sender)
    client.put(
        "/api/v1/settings/notifications/email",
        json=smtp_update().model_dump(mode="json"),
        headers=headers,
    )

    response = client.post(
        "/api/v1/settings/notifications/email/test", headers=headers
    )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "smtp_test_failed"
    assert sender.calls == [
        ("owner@example.test", "Quantitative Trading email test", "SMTP configuration test succeeded.")
    ]
    response_text = response.text.lower()
    assert SYNTHETIC_PASSWORD not in response_text
    assert "synthetic-token" not in response_text
    assert "/tmp/mail.log" not in response_text
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=20)
    audit_text = " ".join(item.model_dump_json() for item in audits).lower()
    assert "smtp.test.failed" in audit_text
    assert SYNTHETIC_PASSWORD not in audit_text
    assert "synthetic-token" not in audit_text


def test_smtplib_connection_test_authenticates_without_sending(
    smtp_service,
    monkeypatch,
) -> None:
    service, repository = smtp_service
    service.update(smtp_update(), now=NOW)
    configured = repository.get()
    assert configured is not None
    events: list[object] = []

    class FakeSmtpClient:
        def __init__(self, host: str, port: int, *, timeout: float) -> None:
            events.append(("connect", host, port, timeout))

        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, exc, traceback) -> None:  # noqa: ANN001
            events.append("exit")

        def starttls(self) -> None:
            events.append("starttls")

        def login(self, username: str, password: str) -> None:
            events.append(("login", username, password))

        def send_message(self, message) -> None:  # noqa: ANN001
            events.append("send_message")

    monkeypatch.setattr(email_service_module.smtplib, "SMTP", FakeSmtpClient)

    SmtplibEmailSender(timeout_seconds=3).test_connection(configured)

    assert events == [
        ("connect", "smtp.example.test", 587, 3),
        "enter",
        "starttls",
        ("login", "robot@example.test", SYNTHETIC_PASSWORD),
        "exit",
    ]


def test_smtp_connection_test_api_authenticates_without_sending_and_keeps_email_test(
    tmp_path,
) -> None:
    sender = RecordingEmailSender()
    tester = RecordingConnectionTester()
    client, headers, settings = authenticated_client(
        tmp_path,
        sender=sender,
        connection_tester=tester,
    )
    client.put(
        "/api/v1/settings/notifications/email",
        json=smtp_update().model_dump(mode="json"),
        headers=headers,
    )

    unauthorized = client.post(
        "/api/v1/notifications/email/settings/test-connection"
    )
    connected = client.post(
        "/api/v1/notifications/email/settings/test-connection",
        headers=headers,
    )

    assert unauthorized.status_code == 401
    assert connected.status_code == 200
    assert connected.json() == {"status": "connected"}
    assert tester.calls == [
        (
            "smtp.example.test",
            587,
            "robot@example.test",
            SmtpSecurity.STARTTLS,
        )
    ]
    assert sender.calls == []
    assert SYNTHETIC_PASSWORD not in connected.text

    sent = client.post(
        "/api/v1/settings/notifications/email/test",
        headers=headers,
    )
    assert sent.status_code == 200
    assert sent.json() == {"status": "sent"}
    assert sender.calls == [
        (
            "owner@example.test",
            "Quantitative Trading email test",
            "SMTP configuration test succeeded.",
        )
    ]

    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=20)
    connection_audit = next(
        item
        for item in audits
        if item.event_type == "smtp.connection_test.succeeded"
    )
    assert connection_audit.payload == {
        "host": "smtp.example.test",
        "port": 587,
        "security": "starttls",
    }
    assert SYNTHETIC_PASSWORD not in " ".join(
        item.model_dump_json() for item in audits
    )


def test_smtp_connection_test_api_sanitizes_failures_and_requires_configuration(
    tmp_path,
) -> None:
    tester = RecordingConnectionTester(
        RuntimeError(
            f"login failed password={SYNTHETIC_PASSWORD} "
            "token=synthetic-token /tmp/mail.log"
        )
    )
    client, headers, settings = authenticated_client(
        tmp_path,
        connection_tester=tester,
    )

    missing = client.post(
        "/api/v1/notifications/email/settings/test-connection",
        headers=headers,
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "smtp_not_configured"

    client.put(
        "/api/v1/settings/notifications/email",
        json=smtp_update().model_dump(mode="json"),
        headers=headers,
    )
    failed = client.post(
        "/api/v1/notifications/email/settings/test-connection",
        headers=headers,
    )

    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "smtp_connection_test_failed"
    response_text = failed.text.lower()
    assert SYNTHETIC_PASSWORD not in response_text
    assert "synthetic-token" not in response_text
    assert "/tmp/mail.log" not in response_text
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=20)
    audit_text = " ".join(item.model_dump_json() for item in audits).lower()
    assert "smtp.connection_test.failed" in audit_text
    assert SYNTHETIC_PASSWORD not in audit_text
    assert "synthetic-token" not in audit_text
    assert "/tmp/mail.log" not in audit_text
