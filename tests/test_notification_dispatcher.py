import json
import sqlite3
import subprocess
import sys
import textwrap
import threading
from datetime import UTC, datetime, timedelta

import pytest

from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.audit.service import AuditService
from quantitative_trading.config import Settings
from quantitative_trading.email.models import SmtpSecurity, SmtpSettingsUpdate
from quantitative_trading.email.outbox import EmailDeliveryRepository, EmailDeliveryService
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import SmtpSettingsService
from quantitative_trading.notification.dispatcher import NotificationDispatcher
from quantitative_trading.notification.jsonl import JsonlNotificationWriter
from quantitative_trading.notification.local_alert import LocalAlertDispatcher
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.notification.service import NotificationService
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 13, 7, 0, tzinfo=UTC)


def test_jsonl_writer_module_loads_when_fcntl_is_unavailable() -> None:
    script = textwrap.dedent(
        """
        import builtins
        import importlib

        original_import = builtins.__import__

        def without_fcntl(name, *args, **kwargs):
            if name == "fcntl":
                raise ModuleNotFoundError("No module named 'fcntl'")
            return original_import(name, *args, **kwargs)

        builtins.__import__ = without_fcntl
        importlib.import_module("quantitative_trading.notification.jsonl")
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr


class NoopSender:
    def send(self, settings, *, recipient: str, subject: str, body: str) -> None:  # noqa: ANN001
        pass


def recommendation(
    recommendation_id: str,
    action: RecommendationAction,
    *,
    risk_note: str = "base risk",
    invalid_if: str = "condition invalidated",
    suggested_quantity: int = 100,
    plan_id: str = "plan-20260713-v1",
    created_at: datetime = NOW,
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        symbol="600000",
        name="Pufa Bank",
        action=action,
        confidence="medium",
        position_context={"source": "manual_ledger", "quantity": 1000},
        account_context={"source": "manual_cash_account"},
        price_context={"current_price": 10.5},
        reason=["condition matched"],
        risk={
            "position_limit": "single <= 30%",
            "invalid_if": [invalid_if],
            "notes": [risk_note],
        },
        valid_until=NOW + timedelta(hours=8),
        data_time=NOW,
        created_at=created_at,
        plan_id=plan_id,
        position_constraint={
            "suggested_quantity": suggested_quantity,
            "max_position_ratio": 0.30,
        },
    )


def configure_smtp(connection) -> None:  # noqa: ANN001
    SmtpSettingsService(SmtpSettingsRepository(connection)).update(
        SmtpSettingsUpdate(
            host="smtp.example.test",
            port=587,
            username="robot@example.test",
            password="synthetic-dispatch-password",
            sender="robot@example.test",
            recipient="owner@example.test",
            security=SmtpSecurity.STARTTLS,
            enabled=True,
        ),
        now=NOW,
    )


def build_dispatcher(connection, settings, *, writer=None, outbox=None):  # noqa: ANN001
    audit_repository = AuditLogRepository(connection)
    notification_service = NotificationService(NotificationRepository(connection))
    audit_service = AuditService(
        audit_repository,
        configured_secret_texts=("synthetic-dispatch-password",),
    )
    jsonl_writer = writer or JsonlNotificationWriter(
        settings,
        configured_secret_texts=("synthetic-dispatch-password",),
    )
    local_alert_dispatcher = LocalAlertDispatcher(
        notification_service=notification_service,
        audit_service=audit_service,
        jsonl_writer=jsonl_writer,
        configured_secret_texts=("synthetic-dispatch-password",),
    )
    return NotificationDispatcher(
        notification_service=notification_service,
        audit_service=audit_service,
        jsonl_writer=jsonl_writer,
        email_service=outbox
        or EmailDeliveryService(
            EmailDeliveryRepository(connection),
            SmtpSettingsRepository(connection),
            NoopSender(),
            audit_repository=audit_repository,
        ),
        smtp_settings_service=SmtpSettingsService(SmtpSettingsRepository(connection)),
        local_alert_dispatcher=local_alert_dispatcher,
    )


@pytest.mark.parametrize(
    "action",
    [
        RecommendationAction.BUY,
        RecommendationAction.ADD,
        RecommendationAction.SELL,
        RecommendationAction.REDUCE,
    ],
)
def test_immediate_actions_create_notification_jsonl_and_email_outbox(tmp_path, action) -> None:
    settings = Settings(
        database_path=tmp_path / f"{action.value}.db",
        log_dir=tmp_path / f"logs-{action.value}",
    )
    with connect(settings) as connection:
        migrate(connection)
        configure_smtp(connection)
        result = build_dispatcher(connection, settings).dispatch_recommendation(
            recommendation(f"rec-{action.value}", action),
            plan_version=1,
            now=NOW,
        )

        assert result.created is True
        assert result.notification.action == action.value
        assert result.email_delivery is not None
        assert result.email_delivery.notification_id == result.notification.notification_id
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 1
        records = (settings.log_dir / "notifications.jsonl").read_text().splitlines()
        assert len(records) == 1
        assert json.loads(records[0])["summary"]["notification_id"] == result.notification.notification_id


def test_retry_after_local_commit_repairs_missing_projections_without_duplicates(
    tmp_path,
) -> None:
    settings = Settings(
        database_path=tmp_path / "projection-recovery.db",
        log_dir=tmp_path / "logs",
    )
    rec = recommendation("rec-projection-recovery", RecommendationAction.BUY)

    with connect(settings) as connection:
        migrate(connection)
        configure_smtp(connection)
        dispatcher = build_dispatcher(connection, settings)

        local = dispatcher.persist_local_recommendation(
            rec,
            plan_version=1,
            now=NOW,
        )
        assert local.created is True
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 0
        assert not (settings.log_dir / "notifications.jsonl").exists()

        repaired = dispatcher.dispatch_recommendation(
            rec,
            plan_version=1,
            now=NOW,
        )
        duplicate = dispatcher.dispatch_recommendation(
            rec,
            plan_version=1,
            now=NOW,
        )

        records = (settings.log_dir / "notifications.jsonl").read_text().splitlines()
        assert repaired.created is False
        assert repaired.email_delivery is not None
        assert duplicate.created is False
        assert duplicate.email_delivery == repaired.email_delivery
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 1
        assert len(records) == 1
        assert json.loads(records[0])["summary"]["notification_id"] == local.notification.notification_id


@pytest.mark.parametrize(
    "action",
    [
        RecommendationAction.HOLD,
        RecommendationAction.WATCH,
        RecommendationAction.AVOID,
    ],
)
def test_non_immediate_actions_create_local_notification_without_email(tmp_path, action) -> None:
    settings = Settings(
        database_path=tmp_path / f"{action.value}.db",
        log_dir=tmp_path / f"logs-{action.value}",
    )
    with connect(settings) as connection:
        migrate(connection)
        configure_smtp(connection)

        result = build_dispatcher(connection, settings).dispatch_recommendation(
            recommendation(f"rec-{action.value}", action),
            plan_version=1,
            now=NOW,
        )

        assert result.notification.action == action.value
        assert result.email_delivery is None
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 0


def test_condition_fingerprint_deduplicates_same_and_allows_material_changes(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "dedup.db", log_dir=tmp_path / "logs")
    with connect(settings) as connection:
        migrate(connection)
        configure_smtp(connection)
        dispatcher = build_dispatcher(connection, settings)

        first_rec = recommendation("rec-1", RecommendationAction.REDUCE)
        duplicate_rec = recommendation("rec-duplicate", RecommendationAction.REDUCE)
        next_cycle_rec = recommendation(
            "rec-next-cycle",
            RecommendationAction.REDUCE,
            created_at=NOW + timedelta(minutes=3),
        )
        changed_risk_rec = recommendation(
            "rec-risk",
            RecommendationAction.REDUCE,
            risk_note="new risk",
        )
        changed_invalidation_rec = recommendation(
            "rec-invalid",
            RecommendationAction.REDUCE,
            invalid_if="different invalidation",
        )
        changed_position_rec = recommendation(
            "rec-position",
            RecommendationAction.REDUCE,
            suggested_quantity=200,
        )
        RecommendationRepository(connection).save_many(
            [
                first_rec,
                duplicate_rec,
                next_cycle_rec,
                changed_risk_rec,
                changed_invalidation_rec,
                changed_position_rec,
            ],
            created_at=NOW,
        )

        first = dispatcher.dispatch_recommendation(
            first_rec,
            plan_version=1,
            now=NOW,
        )
        duplicate = dispatcher.dispatch_recommendation(
            duplicate_rec,
            plan_version=1,
            now=NOW,
        )
        next_cycle = dispatcher.dispatch_recommendation(
            next_cycle_rec,
            plan_version=1,
            now=NOW + timedelta(minutes=3),
        )
        changed_risk = dispatcher.dispatch_recommendation(
            changed_risk_rec,
            plan_version=1,
            now=NOW,
        )
        changed_invalidation = dispatcher.dispatch_recommendation(
            changed_invalidation_rec,
            plan_version=1,
            now=NOW,
        )
        changed_position = dispatcher.dispatch_recommendation(
            changed_position_rec,
            plan_version=1,
            now=NOW,
        )

        assert duplicate.created is False
        assert next_cycle.created is False
        assert duplicate.notification == first.notification
        assert next_cycle.notification == first.notification
        assert changed_risk.notification != first.notification
        assert changed_invalidation.notification != first.notification
        assert changed_position.notification != first.notification
        links = NotificationRepository(connection)
        assert links.get_link(first_rec.recommendation_id).notification_id == first.notification.notification_id
        assert links.get_link(duplicate_rec.recommendation_id).notification_id == first.notification.notification_id
        assert links.get_link(next_cycle_rec.recommendation_id).notification_id == first.notification.notification_id
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 4
        assert len((settings.log_dir / "notifications.jsonl").read_text().splitlines()) == 4


def test_condition_fingerprint_normalizes_structured_json_values() -> None:
    rec = recommendation("rec-structured", RecommendationAction.REDUCE).model_copy(
        update={
            "position_constraint": {
                "suggested_quantity": 100,
                "review_at": NOW,
            }
        }
    )

    first = NotificationDispatcher.condition_fingerprint(rec)
    second = NotificationDispatcher.condition_fingerprint(rec)

    assert first == second
    assert len(first) == 64


def test_concurrent_same_condition_creates_one_notification_and_audit(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "concurrent.db", log_dir=tmp_path / "logs")
    with connect(settings) as connection:
        migrate(connection)

    barrier = threading.Barrier(2)
    created: list[bool] = []
    failures: list[BaseException] = []

    def dispatch(index: int) -> None:
        try:
            with connect(settings) as connection:
                rec = recommendation(
                    f"rec-concurrent-{index}",
                    RecommendationAction.HOLD,
                )
                RecommendationRepository(connection).save_many([rec], created_at=NOW)
                dispatcher = build_dispatcher(connection, settings)
                barrier.wait(timeout=5)
                result = dispatcher.persist_local_recommendation(
                    rec,
                    plan_version=1,
                    now=NOW,
                )
                created.append(result.created)
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=dispatch, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not failures
    assert sorted(created) == [False, True]
    with connect(settings) as connection:
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM notification_canonical_groups").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM recommendation_notification_links").fetchone()[0] == 2
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE event_type='notification.created'"
        ).fetchone()[0] == 1


def test_notification_conflict_preserves_outer_savepoint_and_recommendation(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "outer-savepoint.db", log_dir=tmp_path / "logs")
    with connect(settings) as connection:
        migrate(connection)
        first_rec = recommendation("rec-first", RecommendationAction.HOLD)
        RecommendationRepository(connection).save_many([first_rec], created_at=NOW)
        dispatcher = build_dispatcher(connection, settings)
        first = dispatcher.persist_local_recommendation(
            first_rec,
            plan_version=1,
            now=NOW,
        )

        second_rec = recommendation("rec-second", RecommendationAction.HOLD)
        connection.execute("SAVEPOINT outer_stage")
        RecommendationRepository(connection).save_many(
            [second_rec], created_at=NOW, commit=False
        )
        repository = dispatcher.notification_service.repository
        original_get = repository.get_canonical_group
        missed_lookups = 0

        def miss_twice(canonical_key: str):
            nonlocal missed_lookups
            if missed_lookups < 2:
                missed_lookups += 1
                return None
            return original_get(canonical_key)

        repository.get_canonical_group = miss_twice
        recovered = dispatcher.persist_local_recommendation(
            second_rec,
            plan_version=1,
            now=NOW,
            commit=False,
        )
        connection.execute("RELEASE SAVEPOINT outer_stage")
        connection.commit()

        assert recovered.created is False
        assert recovered.notification == first.notification
        assert RecommendationRepository(connection).get(second_rec.recommendation_id) is not None
        assert repository.get_link(second_rec.recommendation_id) is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE event_type='notification.created'"
        ).fetchone()[0] == 1


def test_notification_canonical_key_uses_decision_day_not_stale_data_day(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "decision-day.db", log_dir=tmp_path / "logs")
    next_day = NOW + timedelta(days=1)
    with connect(settings) as connection:
        migrate(connection)
        first_rec = recommendation("rec-day-1", RecommendationAction.HOLD)
        next_rec = recommendation(
            "rec-day-2",
            RecommendationAction.HOLD,
            created_at=next_day,
        ).model_copy(update={"data_time": first_rec.data_time})
        RecommendationRepository(connection).save_many(
            [first_rec, next_rec], created_at=NOW
        )
        dispatcher = build_dispatcher(connection, settings)

        first = dispatcher.persist_local_recommendation(
            first_rec, plan_version=1, now=NOW
        )
        second = dispatcher.persist_local_recommendation(
            next_rec, plan_version=1, now=next_day
        )

        assert first.notification != second.notification
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 2

        no_plan_rec = recommendation(
            "rec-no-plan",
            RecommendationAction.HOLD,
            plan_id=None,
            created_at=next_day,
        )
        RecommendationRepository(connection).save_many(
            [no_plan_rec], created_at=next_day
        )
        no_plan = dispatcher.persist_local_recommendation(
            no_plan_rec,
            plan_version=None,
            now=next_day,
        )
        assert ":no-plan:no-version:" in no_plan.notification.dedup_key


def test_notification_canonical_key_separates_plan_id_and_plan_version(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "plan-boundaries.db",
        log_dir=tmp_path / "logs",
    )
    recommendations = [
        recommendation(
            "rec-plan-a-v1",
            RecommendationAction.HOLD,
            plan_id="plan-a",
        ),
        recommendation(
            "rec-plan-b-v1",
            RecommendationAction.HOLD,
            plan_id="plan-b",
        ),
        recommendation(
            "rec-plan-a-v2",
            RecommendationAction.HOLD,
            plan_id="plan-a",
        ),
    ]

    with connect(settings) as connection:
        migrate(connection)
        RecommendationRepository(connection).save_many(recommendations, created_at=NOW)
        dispatcher = build_dispatcher(connection, settings)
        assert len(
            {dispatcher.condition_fingerprint(item) for item in recommendations}
        ) == 1

        plan_a_v1 = dispatcher.persist_local_recommendation(
            recommendations[0], plan_version=1, now=NOW
        )
        plan_b_v1 = dispatcher.persist_local_recommendation(
            recommendations[1], plan_version=1, now=NOW
        )
        plan_a_v2 = dispatcher.persist_local_recommendation(
            recommendations[2], plan_version=2, now=NOW
        )

        notifications = [
            plan_a_v1.notification,
            plan_b_v1.notification,
            plan_a_v2.notification,
        ]
        assert all(result.created for result in [plan_a_v1, plan_b_v1, plan_a_v2])
        assert len({item.notification_id for item in notifications}) == 3
        assert len({item.dedup_key for item in notifications}) == 3
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 3
        assert connection.execute(
            "SELECT COUNT(*) FROM notification_canonical_groups"
        ).fetchone()[0] == 3

        repository = NotificationRepository(connection)
        for rec, notification in zip(recommendations, notifications, strict=True):
            link = repository.get_link(rec.recommendation_id)
            assert link is not None
            assert link.notification_id == notification.notification_id
            assert link.canonical_key == notification.dedup_key


def test_daily_summary_aggregates_hold_watch_avoid_once_per_plan_version(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "summary.db", log_dir=tmp_path / "logs")
    recommendations = [
        recommendation("rec-hold", RecommendationAction.HOLD),
        recommendation("rec-watch", RecommendationAction.WATCH),
        recommendation("rec-avoid", RecommendationAction.AVOID),
        recommendation("rec-buy", RecommendationAction.BUY),
    ]
    with connect(settings) as connection:
        migrate(connection)
        configure_smtp(connection)
        dispatcher = build_dispatcher(connection, settings)

        first = dispatcher.dispatch_daily_summary(
            plan_id="plan-20260713",
            plan_version=1,
            recommendations=recommendations,
            now=NOW,
        )
        duplicate = dispatcher.dispatch_daily_summary(
            plan_id="plan-20260713",
            plan_version=1,
            recommendations=recommendations,
            now=NOW,
        )
        next_version = dispatcher.dispatch_daily_summary(
            plan_id="plan-20260713",
            plan_version=2,
            recommendations=recommendations,
            now=NOW,
        )

        assert duplicate == first
        assert next_version.delivery_id != first.delivery_id
        assert first.payload["counts"] == {"hold": 1, "watch": 1, "avoid": 1}
        assert "buy" not in first.payload["counts"]
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 2


def test_critical_system_alert_reaches_every_local_channel_without_smtp(
    tmp_path,
    caplog,
) -> None:
    settings = Settings(database_path=tmp_path / "alert.db", log_dir=tmp_path / "logs")
    with connect(settings) as connection:
        migrate(connection)
        dispatcher = build_dispatcher(connection, settings)

        with caplog.at_level("ERROR"):
            first = dispatcher.dispatch_system_alert(
                alert_key="database-integrity-20260713",
                event_type="workflow.database_failed",
                message=(
                    "database failed password=synthetic-dispatch-password "
                    "token=synthetic-token /tmp/private.db"
                ),
                details={"password": "synthetic-dispatch-password"},
                now=NOW,
            )
            duplicate = dispatcher.dispatch_system_alert(
                alert_key="database-integrity-20260713",
                event_type="workflow.database_failed",
                message="same alert retry",
                now=NOW,
            )

        assert first is None
        assert duplicate is None
        notifications = NotificationRepository(connection).list_recent(limit=20)
        assert len(notifications) == 1
        alert = notifications[0]
        assert alert.action == "system_alert"
        assert alert.symbol == "000000"
        assert alert.reason == ["database failed [redacted] [redacted] [path]"]
        assert alert.status.value == "unread"
        records = (settings.log_dir / "notifications.jsonl").read_text().splitlines()
        assert len(records) == 1
        record = json.loads(records[0])
        assert record["summary"]["notification_id"] == alert.notification_id
        assert record["system_alert"]["event_type"] == "workflow.database_failed"
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 0
        local_outputs = "\n".join(records + [caplog.text]).lower()
        assert "workflow.database_failed" in caplog.text
        assert "synthetic-dispatch-password" not in local_outputs
        assert "synthetic-token" not in local_outputs
        assert "/tmp/private.db" not in local_outputs
        audits = AuditLogRepository(connection).list_recent(limit=20)
        assert sum(item.event_type == "workflow.database_failed" for item in audits) == 1


def test_system_alert_retry_repairs_missing_jsonl_projection_without_duplicates(
    tmp_path,
) -> None:
    settings = Settings(
        database_path=tmp_path / "alert-projection-recovery.db",
        log_dir=tmp_path / "logs",
    )
    delegate = JsonlNotificationWriter(settings)

    class FailingOnceWriter:
        calls = 0

        def write_system_alert(self, *args, **kwargs):  # noqa: ANN002, ANN003
            self.calls += 1
            if self.calls == 1:
                raise OSError("jsonl temporarily unavailable")
            return delegate.write_system_alert(*args, **kwargs)

    with connect(settings) as connection:
        migrate(connection)
        local_dispatcher = build_dispatcher(
            connection,
            settings,
            writer=FailingOnceWriter(),
        ).local_alert_dispatcher

        first = local_dispatcher.dispatch(
            alert_key="workflow-close-20260713",
            event_type="workflow.close_failed",
            message="close workflow failed",
            details={"run_id": "run-close-1"},
            now=NOW,
        )
        retry = local_dispatcher.dispatch(
            alert_key="workflow-close-20260713",
            event_type="workflow.close_failed",
            message="close workflow failed",
            details={"run_id": "run-close-1"},
            now=NOW,
        )
        duplicate = local_dispatcher.dispatch(
            alert_key="workflow-close-20260713",
            event_type="workflow.close_failed",
            message="close workflow failed",
            details={"run_id": "run-close-1"},
            now=NOW,
        )

        records = (settings.log_dir / "notifications.jsonl").read_text().splitlines()
        assert retry == first
        assert duplicate == first
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        assert len(records) == 1
        assert json.loads(records[0])["summary"]["notification_id"] == first.notification_id


def test_critical_system_alert_email_is_immediate_and_deduplicated(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "alert-email.db", log_dir=tmp_path / "logs")
    with connect(settings) as connection:
        migrate(connection)
        configure_smtp(connection)
        dispatcher = build_dispatcher(connection, settings)

        first = dispatcher.dispatch_system_alert(
            alert_key="database-integrity-20260713",
            event_type="workflow.database_failed",
            message="database failed synthetic-dispatch-password",
            details={"safe_detail": "synthetic-dispatch-password"},
            now=NOW,
        )
        duplicate = dispatcher.dispatch_system_alert(
            alert_key="database-integrity-20260713",
            event_type="workflow.database_failed",
            message="same alert retry",
            now=NOW,
        )

        assert duplicate == first
        assert first.notification_id is not None
        persisted = first.model_dump_json().lower()
        assert "synthetic-dispatch-password" not in persisted
        assert connection.execute("SELECT COUNT(*) FROM email_deliveries").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 1
        jsonl_text = (settings.log_dir / "notifications.jsonl").read_text()
        assert "synthetic-dispatch-password" not in jsonl_text
        audits = AuditLogRepository(connection).list_recent(limit=20)
        assert any(item.event_type == "workflow.database_failed" for item in audits)


def test_jsonl_and_outbox_failures_do_not_rollback_recommendation_or_notification(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "failure.db", log_dir=tmp_path / "logs")
    rec = recommendation("rec-1", RecommendationAction.SELL)

    class FailingWriter:
        def write(self, summary, recommendation, audit_ref):  # noqa: ANN001
            raise OSError(
                "jsonl unavailable synthetic-dispatch-password "
                "token=synthetic-token /tmp/private.log"
            )

    class FailingOutbox:
        def get_by_dedup_key(self, dedup_key):  # noqa: ANN001
            return None

        def enqueue(self, **kwargs):  # noqa: ANN003
            raise sqlite3.OperationalError(
                "outbox unavailable synthetic-dispatch-password /tmp/private.db"
            )

    with connect(settings) as connection:
        migrate(connection)
        configure_smtp(connection)
        RecommendationRepository(connection).save_many([rec], created_at=NOW)
        result = build_dispatcher(
            connection,
            settings,
            writer=FailingWriter(),
            outbox=FailingOutbox(),
        ).dispatch_recommendation(rec, plan_version=1, now=NOW)

        assert RecommendationRepository(connection).get("rec-1") == rec
        assert NotificationRepository(connection).get(result.notification.notification_id) is not None
        assert result.email_delivery is None
        assert len(result.warnings) == 2
        warning_text = " ".join(result.warnings).lower()
        assert "synthetic-token" not in warning_text
        assert "synthetic-dispatch-password" not in warning_text
        assert "/tmp/private" not in warning_text
        audits = AuditLogRepository(connection).list_recent(limit=20)
        event_types = {item.event_type for item in audits}
        assert "notification.jsonl_failed" in event_types
        assert "email.outbox_failed" in event_types


def test_daily_summary_outbox_failure_is_audited_without_escaping(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "summary-failure.db")

    class FailingOutbox:
        def get_by_dedup_key(self, dedup_key):  # noqa: ANN001
            return None

        def enqueue(self, **kwargs):  # noqa: ANN003
            raise sqlite3.OperationalError(
                "summary outbox unavailable synthetic-dispatch-password /tmp/private.db"
            )

    with connect(settings) as connection:
        migrate(connection)
        configure_smtp(connection)
        result = build_dispatcher(
            connection,
            settings,
            outbox=FailingOutbox(),
        ).dispatch_daily_summary(
            plan_id="plan-20260713",
            plan_version=1,
            recommendations=[
                recommendation("rec-hold", RecommendationAction.HOLD)
            ],
            now=NOW,
        )

        assert result is None
        audits = AuditLogRepository(connection).list_recent(limit=20)
        failure = next(
            item for item in audits if item.event_type == "email.daily_summary_failed"
        )
        audit_text = failure.model_dump_json()
        assert "synthetic-dispatch-password" not in audit_text
        assert "/tmp/private.db" not in audit_text
