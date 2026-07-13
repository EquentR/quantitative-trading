from __future__ import annotations

import smtplib
from collections.abc import Sequence
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Protocol

from quantitative_trading.email.models import (
    SmtpSecurity,
    SmtpSettings,
    SmtpSettingsPublic,
    SmtpSettingsUpdate,
)
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.sanitization import safe_error_summary


class EmailSender(Protocol):
    def send(
        self,
        settings: SmtpSettings,
        *,
        recipient: str,
        subject: str,
        body: str,
    ) -> None: ...


class SmtpConnectionTester(Protocol):
    def test_connection(self, settings: SmtpSettings) -> None: ...


class SmtpSettingsNotConfiguredError(RuntimeError):
    pass


class SmtplibEmailSender:
    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = timeout_seconds

    def send(
        self,
        settings: SmtpSettings,
        *,
        recipient: str,
        subject: str,
        body: str,
    ) -> None:
        message = EmailMessage()
        message["From"] = settings.sender
        message["To"] = recipient
        message["Subject"] = subject
        message.set_content(body)

        with self._open_client(settings) as client:
            self._authenticate(client, settings)
            client.send_message(message)

    def test_connection(self, settings: SmtpSettings) -> None:
        with self._open_client(settings) as client:
            self._authenticate(client, settings)

    def _open_client(self, settings: SmtpSettings):
        smtp_type = (
            smtplib.SMTP_SSL
            if settings.security is SmtpSecurity.SSL
            else smtplib.SMTP
        )
        return smtp_type(
            settings.host,
            settings.port,
            timeout=self.timeout_seconds,
        )

    @staticmethod
    def _authenticate(client, settings: SmtpSettings) -> None:
        if settings.security is SmtpSecurity.STARTTLS:
            client.starttls()
        if settings.username:
            client.login(settings.username, settings.password or "")


class SmtpSettingsService:
    def __init__(self, repository: SmtpSettingsRepository) -> None:
        self.repository = repository

    def get_public(self) -> SmtpSettingsPublic:
        settings = self.repository.get()
        if settings is None:
            return SmtpSettingsPublic(configured=False)
        return self._public(settings)

    def delivery_recipient(self) -> str | None:
        settings = self.repository.get()
        if settings is None or not settings.enabled:
            return None
        return settings.recipient

    def sanitized_error(self, exc: Exception) -> str:
        settings = self.repository.get()
        secret_texts = (
            (settings.password,)
            if settings is not None and settings.password
            else ()
        )
        return sanitized_email_error(exc, secret_texts=secret_texts)

    def update(
        self,
        update: SmtpSettingsUpdate,
        *,
        now: datetime | None = None,
        commit: bool = True,
    ) -> SmtpSettingsPublic:
        existing = self.repository.get()
        submitted_password = update.password
        password = (
            existing.password
            if submitted_password is None or submitted_password == ""
            else submitted_password
        ) if existing is not None else (submitted_password or None)
        settings = SmtpSettings(
            host=update.host,
            port=update.port,
            username=update.username,
            password=password,
            sender=update.sender,
            recipient=update.recipient,
            security=update.security,
            enabled=update.enabled,
            updated_at=now or datetime.now(UTC),
        )
        self.repository.save(settings, commit=commit)
        return self._public(settings)

    def clear_password(
        self,
        *,
        now: datetime | None = None,
        commit: bool = True,
    ) -> SmtpSettingsPublic:
        existing = self.repository.get()
        if existing is None:
            return SmtpSettingsPublic(configured=False)
        updated = existing.model_copy(
            update={"password": None, "updated_at": now or datetime.now(UTC)}
        )
        self.repository.save(updated, commit=commit)
        return self._public(updated)

    def send_test(self, sender: EmailSender) -> SmtpSettings:
        settings = self.repository.get()
        if settings is None:
            raise SmtpSettingsNotConfiguredError("SMTP settings are not configured")
        sender.send(
            settings,
            recipient=settings.recipient,
            subject="Quantitative Trading email test",
            body="SMTP configuration test succeeded.",
        )
        return settings

    def test_connection(self, tester: SmtpConnectionTester) -> SmtpSettings:
        settings = self.repository.get()
        if settings is None:
            raise SmtpSettingsNotConfiguredError("SMTP settings are not configured")
        tester.test_connection(settings)
        return settings

    @staticmethod
    def _public(settings: SmtpSettings) -> SmtpSettingsPublic:
        return SmtpSettingsPublic(
            configured=True,
            host=settings.host,
            port=settings.port,
            username=settings.username,
            sender=settings.sender,
            recipient=settings.recipient,
            security=settings.security,
            enabled=settings.enabled,
            password_configured=bool(settings.password),
            updated_at=settings.updated_at,
        )


def sanitized_email_error(exc: Exception, *, secret_texts: Sequence[str] = ()) -> str:
    summary = safe_error_summary(exc)
    for secret in secret_texts:
        if secret:
            summary = summary.replace(secret, "[redacted]")
    return summary
