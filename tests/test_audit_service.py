from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 9, 2, 30, tzinfo=UTC)


@pytest.fixture
def service(tmp_path) -> Iterator[AuditService]:
    settings = Settings(database_path=tmp_path / "audit.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = AuditLogRepository(connection)
        yield AuditService(repository, id_factory=lambda: "audit-1")


def test_create_event_persists_audit_log(service: AuditService) -> None:
    audit = service.record_event(
        event_type="notification.created",
        recommendation_id="rec-1",
        payload={"channel": "local", "attempt": 1},
        now=NOW,
    )

    restored = service.get("audit-1")

    assert audit == AuditLog(
        audit_id="audit-1",
        event_type="notification.created",
        recommendation_id="rec-1",
        payload={"channel": "local", "attempt": 1},
        created_at=NOW,
    )
    assert restored == audit


def test_list_recent_returns_newest_audit_first(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "audit.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = AuditLogRepository(connection)
        first = AuditService(repository, id_factory=lambda: "audit-1").record_event(
            event_type="recommendation.generated",
            recommendation_id="rec-1",
            payload={},
            now=NOW,
        )
        second = AuditService(repository, id_factory=lambda: "audit-2").record_event(
            event_type="notification.created",
            recommendation_id="rec-1",
            payload={},
            now=datetime(2026, 7, 9, 2, 31, tzinfo=UTC),
        )

        assert repository.list_recent(limit=10) == [second, first]


def test_create_event_sanitizes_sensitive_payload_text(service: AuditService) -> None:
    audit = service.record_event(
        event_type="notification.failed",
        recommendation_id="rec-1",
        payload={
            "api_key": "raw-key",
            "message": "remote failed with token=raw-token cookie=raw-cookie",
            "nested": {"safe": "kept"},
        },
        now=NOW,
    )

    text = audit.model_dump_json().lower()
    assert "api_key" not in text
    assert "token" not in text
    assert "cookie" not in text
    assert "raw-key" not in text
    assert "raw-token" not in text
    assert "raw-cookie" not in text
    assert audit.payload["nested"] == {"safe": "kept"}
