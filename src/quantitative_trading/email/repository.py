from __future__ import annotations

import sqlite3
from datetime import UTC

from quantitative_trading.email.models import SmtpSecurity, SmtpSettings


class SmtpSettingsRepository:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def get(self) -> SmtpSettings | None:
        row = self.connection.execute(
            """
            SELECT host, port, username, password, sender, recipient,
                   security, enabled, updated_at
            FROM smtp_settings
            WHERE id = 1
            """
        ).fetchone()
        if row is None:
            return None
        return SmtpSettings(
            host=row["host"],
            port=row["port"],
            username=row["username"],
            password=row["password"],
            sender=row["sender"],
            recipient=row["recipient"],
            security=SmtpSecurity(row["security"]),
            enabled=bool(row["enabled"]),
            updated_at=row["updated_at"],
        )

    def save(self, settings: SmtpSettings, *, commit: bool = True) -> SmtpSettings:
        self.connection.execute(
            """
            INSERT INTO smtp_settings (
              id, host, port, username, password, sender, recipient,
              security, enabled, updated_at
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              host = excluded.host,
              port = excluded.port,
              username = excluded.username,
              password = excluded.password,
              sender = excluded.sender,
              recipient = excluded.recipient,
              security = excluded.security,
              enabled = excluded.enabled,
              updated_at = excluded.updated_at
            """,
            (
                settings.host,
                settings.port,
                settings.username,
                settings.password,
                settings.sender,
                settings.recipient,
                settings.security.value,
                int(settings.enabled),
                settings.updated_at.astimezone(UTC).isoformat(),
            ),
        )
        if commit:
            self.connection.commit()
        return settings
