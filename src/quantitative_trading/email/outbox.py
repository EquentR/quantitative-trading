from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.email.models import EmailDelivery, EmailDeliveryStatus
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import EmailSender, sanitized_email_error
from quantitative_trading.sanitization import sanitize_sensitive_data


DEFAULT_RETRY_DELAYS_MINUTES = (1, 5, 15, 30, 60)
LOGGER = logging.getLogger(__name__)


class EmailDeliveryNotRetryableError(RuntimeError):
    pass


class DeadDeliveryAlert(Protocol):
    def __call__(
        self,
        *,
        delivery: EmailDelivery,
        audit_ref: AuditLog,
        now: datetime,
    ) -> object: ...


class EmailDeliveryRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self, delivery_id: str) -> EmailDelivery | None:
        row = self.connection.execute(
            "SELECT * FROM email_deliveries WHERE delivery_id = ?",
            (delivery_id,),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def get_by_dedup_key(self, dedup_key: str) -> EmailDelivery | None:
        row = self.connection.execute(
            "SELECT * FROM email_deliveries WHERE dedup_key = ?",
            (dedup_key,),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def enqueue(self, delivery: EmailDelivery, *, commit: bool = True) -> EmailDelivery:
        try:
            self.connection.execute(
                """
                INSERT INTO email_deliveries (
                  delivery_id, notification_id, dedup_key, recipient, subject, body,
                  payload_json, status, attempt_count, next_attempt_at,
                  lease_expires_at, last_error, sent_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    delivery.delivery_id,
                    delivery.notification_id,
                    delivery.dedup_key,
                    delivery.recipient,
                    delivery.subject,
                    delivery.body,
                    json.dumps(delivery.payload, ensure_ascii=False, sort_keys=True),
                    delivery.status.value,
                    delivery.attempt_count,
                    self._time(delivery.next_attempt_at),
                    self._time(delivery.lease_expires_at),
                    delivery.last_error,
                    self._time(delivery.sent_at),
                    self._time(delivery.created_at),
                    self._time(delivery.updated_at),
                ),
            )
        except sqlite3.IntegrityError:
            existing = self.get_by_dedup_key(delivery.dedup_key)
            if existing is None:
                raise
            return existing
        if commit:
            self.connection.commit()
        return delivery

    def list(
        self,
        *,
        status: EmailDeliveryStatus | None = None,
        notification_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EmailDelivery]:
        clauses: list[str] = []
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        if notification_id is not None:
            clauses.append("notification_id = ?")
            parameters.append(notification_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.extend((limit, offset))
        rows = self.connection.execute(
            f"""
            SELECT *
            FROM email_deliveries
            {where}
            ORDER BY created_at DESC, delivery_id DESC
            LIMIT ? OFFSET ?
            """,
            parameters,
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def count(
        self,
        *,
        status: EmailDeliveryStatus | None = None,
        notification_id: str | None = None,
    ) -> int:
        clauses: list[str] = []
        parameters: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            parameters.append(status.value)
        if notification_id is not None:
            clauses.append("notification_id = ?")
            parameters.append(notification_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM email_deliveries {where}", parameters
        ).fetchone()
        return int(row["count"])

    def claim_due(
        self,
        *,
        now: datetime,
        lease_seconds: int = 60,
        limit: int = 20,
    ) -> list[EmailDelivery]:
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        rows = self.connection.execute(
            """
            UPDATE email_deliveries
            SET status = 'sending', lease_expires_at = ?, updated_at = ?
            WHERE delivery_id IN (
              SELECT delivery_id
              FROM email_deliveries
              WHERE (
                status IN ('pending', 'retry')
                AND next_attempt_at IS NOT NULL
                AND next_attempt_at <= ?
              ) OR (
                status = 'sending'
                AND lease_expires_at IS NOT NULL
                AND lease_expires_at <= ?
              )
              ORDER BY COALESCE(next_attempt_at, lease_expires_at), created_at, delivery_id
              LIMIT ?
            )
            RETURNING *
            """,
            (
                self._time(lease_expires_at),
                self._time(now),
                self._time(now),
                self._time(now),
                limit,
            ),
        ).fetchall()
        self.connection.commit()
        deliveries = [self._from_row(row) for row in rows]
        return sorted(deliveries, key=lambda item: (item.created_at, item.delivery_id))

    def recover_expired_leases(
        self,
        *,
        now: datetime,
        commit: bool = True,
    ) -> int:
        cursor = self.connection.execute(
            """
            UPDATE email_deliveries
            SET status = 'retry', next_attempt_at = ?, lease_expires_at = NULL,
                updated_at = ?
            WHERE status = 'sending'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (self._time(now), self._time(now), self._time(now)),
        )
        if commit:
            self.connection.commit()
        return cursor.rowcount

    def mark_sent(self, delivery_id: str, *, now: datetime) -> EmailDelivery:
        self.connection.execute(
            """
            UPDATE email_deliveries
            SET status = 'sent', attempt_count = attempt_count + 1,
                next_attempt_at = NULL, lease_expires_at = NULL,
                last_error = '', sent_at = ?, updated_at = ?
            WHERE delivery_id = ? AND status = 'sending'
            """,
            (self._time(now), self._time(now), delivery_id),
        )
        self.connection.commit()
        delivery = self.get(delivery_id)
        if delivery is None:
            raise KeyError(delivery_id)
        return delivery

    def mark_failed(
        self,
        delivery_id: str,
        *,
        error: str,
        now: datetime,
        retry_delays_minutes: Sequence[int] = DEFAULT_RETRY_DELAYS_MINUTES,
    ) -> EmailDelivery:
        delivery = self.get(delivery_id)
        if delivery is None:
            raise KeyError(delivery_id)
        attempt_count = delivery.attempt_count + 1
        if attempt_count > len(retry_delays_minutes):
            status = EmailDeliveryStatus.DEAD
            next_attempt_at = None
        else:
            status = EmailDeliveryStatus.RETRY
            next_attempt_at = now + timedelta(
                minutes=retry_delays_minutes[attempt_count - 1]
            )
        self.connection.execute(
            """
            UPDATE email_deliveries
            SET status = ?, attempt_count = ?, next_attempt_at = ?,
                lease_expires_at = NULL, last_error = ?, updated_at = ?
            WHERE delivery_id = ? AND status = 'sending'
            """,
            (
                status.value,
                attempt_count,
                self._time(next_attempt_at),
                error,
                self._time(now),
                delivery_id,
            ),
        )
        self.connection.commit()
        updated = self.get(delivery_id)
        if updated is None:
            raise KeyError(delivery_id)
        return updated

    def manual_retry(
        self,
        delivery_id: str,
        *,
        now: datetime,
        commit: bool = True,
    ) -> EmailDelivery:
        delivery = self.get(delivery_id)
        if delivery is None:
            raise KeyError(delivery_id)
        if delivery.status not in {EmailDeliveryStatus.RETRY, EmailDeliveryStatus.DEAD}:
            raise EmailDeliveryNotRetryableError(delivery_id)
        self.connection.execute(
            """
            UPDATE email_deliveries
            SET status = 'pending', attempt_count = 0, next_attempt_at = ?,
                lease_expires_at = NULL, last_error = '', sent_at = NULL, updated_at = ?
            WHERE delivery_id = ?
            """,
            (self._time(now), self._time(now), delivery_id),
        )
        if commit:
            self.connection.commit()
        updated = self.get(delivery_id)
        if updated is None:
            raise KeyError(delivery_id)
        return updated

    @staticmethod
    def _time(value: datetime | None) -> str | None:
        return None if value is None else value.astimezone(UTC).isoformat()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> EmailDelivery:
        return EmailDelivery(
            delivery_id=row["delivery_id"],
            notification_id=row["notification_id"],
            dedup_key=row["dedup_key"],
            recipient=row["recipient"],
            subject=row["subject"],
            body=row["body"],
            payload=json.loads(row["payload_json"]),
            status=EmailDeliveryStatus(row["status"]),
            attempt_count=row["attempt_count"],
            next_attempt_at=row["next_attempt_at"],
            lease_expires_at=row["lease_expires_at"],
            last_error=row["last_error"],
            sent_at=row["sent_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class EmailDeliveryService:
    def __init__(
        self,
        repository: EmailDeliveryRepository,
        smtp_repository: SmtpSettingsRepository,
        sender: EmailSender,
        *,
        id_factory: Callable[[], str] | None = None,
        retry_delays_minutes: Sequence[int] = DEFAULT_RETRY_DELAYS_MINUTES,
        lease_seconds: int = 60,
        audit_repository: AuditLogRepository | None = None,
        dead_delivery_alert: DeadDeliveryAlert | None = None,
    ) -> None:
        self.repository = repository
        self.smtp_repository = smtp_repository
        self.sender = sender
        self.id_factory = id_factory or (lambda: f"email-{uuid4().hex}")
        self.retry_delays_minutes = tuple(retry_delays_minutes)
        self.lease_seconds = lease_seconds
        self.audit_repository = audit_repository
        self.dead_delivery_alert = dead_delivery_alert

    def enqueue(
        self,
        *,
        dedup_key: str,
        recipient: str,
        subject: str,
        body: str,
        notification_id: str | None,
        payload: dict[str, object],
        now: datetime | None = None,
    ) -> EmailDelivery:
        settings = self.smtp_repository.get()
        secret_texts = (
            (settings.password,)
            if settings is not None and settings.password
            else ()
        )
        sanitized_dedup_key = sanitize_sensitive_data(
            dedup_key,
            configured_secret_texts=secret_texts,
        )
        existing = self.repository.get_by_dedup_key(sanitized_dedup_key)
        if existing is not None:
            return existing
        created_at = now or datetime.now(UTC)
        sanitized_payload = sanitize_sensitive_data(
            payload,
            configured_secret_texts=secret_texts,
        )
        delivery = EmailDelivery(
            delivery_id=self.id_factory(),
            notification_id=notification_id,
            dedup_key=sanitized_dedup_key,
            recipient=recipient,
            subject=sanitize_sensitive_data(
                subject,
                configured_secret_texts=secret_texts,
            ),
            body=sanitize_sensitive_data(
                body,
                configured_secret_texts=secret_texts,
            ),
            payload=sanitized_payload,
            status=EmailDeliveryStatus.PENDING,
            attempt_count=0,
            next_attempt_at=created_at,
            created_at=created_at,
            updated_at=created_at,
        )
        return self.repository.enqueue(delivery)

    def get_by_dedup_key(self, dedup_key: str) -> EmailDelivery | None:
        return self.repository.get_by_dedup_key(dedup_key)

    def process_due(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[EmailDelivery]:
        attempted_at = now or datetime.now(UTC)
        self.repository.recover_expired_leases(now=attempted_at)
        claimed = self.repository.claim_due(
            now=attempted_at,
            lease_seconds=self.lease_seconds,
            limit=limit,
        )
        settings = self.smtp_repository.get()
        secret_texts = (settings.password,) if settings is not None and settings.password else ()
        processed: list[EmailDelivery] = []
        for delivery in claimed:
            try:
                if settings is None or not settings.enabled:
                    raise RuntimeError("SMTP is not configured or enabled")
                self.sender.send(
                    settings,
                    recipient=delivery.recipient,
                    subject=delivery.subject,
                    body=delivery.body,
                )
            except Exception as exc:
                updated = self.repository.mark_failed(
                    delivery.delivery_id,
                    error=sanitized_email_error(exc, secret_texts=secret_texts),
                    now=attempted_at,
                    retry_delays_minutes=self.retry_delays_minutes,
                )
                processed.append(updated)
                if (
                    updated.status is EmailDeliveryStatus.DEAD
                    and self.audit_repository is not None
                ):
                    audit = AuditService(self.audit_repository).record_event(
                        event_type="email.delivery.dead",
                        recommendation_id=None,
                        payload={
                            "delivery_id": updated.delivery_id,
                            "error": updated.last_error,
                        },
                        now=attempted_at,
                    )
                    if self.dead_delivery_alert is not None:
                        try:
                            self.dead_delivery_alert(
                                delivery=updated,
                                audit_ref=audit,
                                now=attempted_at,
                            )
                        except Exception as alert_exc:
                            LOGGER.error(
                                "email_dead_local_alert_failed delivery_id=%s error=%s",
                                updated.delivery_id,
                                sanitized_email_error(
                                    alert_exc,
                                    secret_texts=secret_texts,
                                ),
                            )
            else:
                processed.append(
                    self.repository.mark_sent(delivery.delivery_id, now=attempted_at)
                )
        return processed

    def list_deliveries(
        self,
        *,
        status: EmailDeliveryStatus | None = None,
        notification_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[EmailDelivery]:
        return self.repository.list(
            status=status,
            notification_id=notification_id,
            limit=limit,
            offset=offset,
        )

    def manual_retry(
        self,
        delivery_id: str,
        *,
        now: datetime | None = None,
    ) -> EmailDelivery:
        retried_at = now or datetime.now(UTC)
        delivery = self.repository.manual_retry(
            delivery_id,
            now=retried_at,
            commit=self.audit_repository is None,
        )
        if self.audit_repository is not None:
            AuditService(self.audit_repository).record_event(
                event_type="email.delivery.retried",
                recommendation_id=None,
                payload={"delivery_id": delivery_id},
                now=retried_at,
                commit=False,
            )
            self.repository.connection.commit()
        return delivery
