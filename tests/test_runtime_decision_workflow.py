from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from quantitative_trading.config import Settings
from quantitative_trading.market.models import CaptureRunStatus


TRADING_TIME = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
CLOSE_TIME = datetime(2026, 7, 14, 7, 20, tzinfo=UTC)


def test_runtime_routes_intraday_to_unified_workflow(tmp_path, monkeypatch) -> None:
    import quantitative_trading.runtime.service_app as service_app

    calls: list[str] = []

    class FakeWorkflow:
        def run_intraday(self):
            calls.append("intraday")
            return SimpleNamespace(
                run_id="intraday-20260714-1000",
                status=CaptureRunStatus.SUCCEEDED,
                market_input_snapshot_id=17,
                recommendation_ids=("rec-1", "rec-2"),
                reused=False,
                warnings=(),
            )

    monkeypatch.setattr(
        service_app,
        "_build_decision_workflow",
        lambda connection, settings, now: FakeWorkflow(),
    )
    settings = Settings(database_path=tmp_path / "runtime.db")

    result = service_app._run_scheduler_task(
        settings,
        "intraday_decision",
        now=TRADING_TIME,
    )

    assert calls == ["intraday"]
    assert result.task_type == "intraday"
    assert result.reason == "intraday_completed"
    assert result.snapshot_id == 17
    assert result.recommendation_ids == ["rec-1", "rec-2"]
    assert result.run_id == "intraday-20260714-1000"


def test_runtime_routes_close_readiness_and_reports_not_ready(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    trade_dates: list[date] = []

    class FakeWorkflow:
        def run_close(self, trade_date: date):
            trade_dates.append(trade_date)
            return SimpleNamespace(
                run_id="close-20260714",
                ready=False,
                reused=False,
                market_input_snapshot_id=23,
                plan_id=None,
                warnings=("日 K 未就绪",),
            )

    monkeypatch.setattr(
        service_app,
        "_build_decision_workflow",
        lambda connection, settings, now: FakeWorkflow(),
    )
    settings = Settings(database_path=tmp_path / "runtime.db")

    result = service_app._run_scheduler_task(
        settings,
        "close_readiness",
        now=CLOSE_TIME,
    )

    assert trade_dates == [date(2026, 7, 14)]
    assert result.status == "degraded"
    assert result.reason == "close_not_ready"
    assert result.snapshot_id == 23
    assert result.plan_id is None
    assert result.run_id == "close-20260714"


def test_runtime_calendar_guard_skips_market_jobs_outside_session(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    monkeypatch.setattr(
        service_app,
        "_build_decision_workflow",
        lambda connection, settings, now: (_ for _ in ()).throw(
            AssertionError("workflow must not run")
        ),
    )
    settings = Settings(database_path=tmp_path / "runtime.db")

    result = service_app._run_scheduler_task(
        settings,
        "intraday_decision",
        now=datetime(2026, 7, 18, 2, 0, tzinfo=UTC),
    )

    assert result.status == "skipped"
    assert result.reason == "not_trading_session"


def test_runtime_runs_cleanup_and_email_worker_independently(
    tmp_path,
    monkeypatch,
) -> None:
    import quantitative_trading.runtime.service_app as service_app

    cleanup_dates: list[date] = []

    class FakeWorkflow:
        def run_cleanup(self, as_of: date):
            cleanup_dates.append(as_of)
            return SimpleNamespace(
                run_id="cleanup-2026-07-14",
                reused=False,
                cleaned_rows=31,
                warnings=(),
            )

    monkeypatch.setattr(
        service_app,
        "_build_decision_workflow",
        lambda connection, settings, now: FakeWorkflow(),
    )

    class FakeEmailWorker:
        def process_due(self, *, now):
            return [SimpleNamespace(delivery_id="email-1")]

    monkeypatch.setattr(
        service_app,
        "_email_delivery_service",
        lambda connection, settings: FakeEmailWorker(),
    )
    settings = Settings(database_path=tmp_path / "runtime.db")

    cleanup = service_app._run_scheduler_task(
        settings,
        "minute_cleanup",
        now=CLOSE_TIME,
    )
    email = service_app._run_scheduler_task(
        settings,
        "email_delivery",
        now=datetime(2026, 7, 18, 2, 0, tzinfo=UTC),
    )

    assert cleanup_dates == [date(2026, 7, 14)]
    assert cleanup.cleaned_rows == 31
    assert cleanup.run_id == "cleanup-2026-07-14"
    assert email.delivery_ids == ["email-1"]
    assert email.status == "success"


def test_startup_recovery_only_catches_current_valid_cycle() -> None:
    import quantitative_trading.runtime.service_app as service_app

    assert service_app._startup_recovery_reason(CLOSE_TIME) == "close_readiness"
    assert service_app._startup_recovery_reason(TRADING_TIME) == "intraday_decision"
    assert (
        service_app._startup_recovery_reason(datetime(2026, 7, 14, 9, 0, tzinfo=UTC))
        is None
    )
    assert (
        service_app._startup_recovery_reason(datetime(2026, 7, 18, 2, 0, tzinfo=UTC))
        is None
    )
