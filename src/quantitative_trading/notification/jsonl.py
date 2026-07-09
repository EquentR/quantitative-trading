from __future__ import annotations

import json

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.config import Settings
from quantitative_trading.notification.models import NotificationSummary
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.sanitization import sanitize_sensitive_data


class JsonlNotificationWriter:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._configured_secrets = tuple(
            value
            for value in (
                settings.api_access_password,
                settings.api_token_secret,
            )
            if value
        )

    def write(
        self,
        summary: NotificationSummary,
        recommendation: Recommendation,
        audit_ref: AuditLog,
    ) -> None:
        self.settings.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.settings.log_dir / "notifications.jsonl"
        payload = {
            "summary": summary.model_dump(mode="json"),
            "recommendation": recommendation.model_dump(mode="json"),
            "audit": audit_ref.model_dump(mode="json"),
        }
        sanitized = sanitize_sensitive_data(
            payload,
            configured_secret_texts=self._configured_secrets,
        )
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(sanitized, ensure_ascii=False))
            log_file.write("\n")
