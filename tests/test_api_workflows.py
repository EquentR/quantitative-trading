from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
import pytest

from quantitative_trading.api.app import create_app
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.config import Settings
from quantitative_trading.decision.workflow import WorkflowAlreadyRunningError
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.models import CaptureRunStatus
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.storage.sqlite import connect
from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedSource
from quantitative_trading.watchlist.repository import WatchPinnedRepository


SHANGHAI = ZoneInfo("Asia/Shanghai")
NORMAL_CLOSE_TIME = datetime(2026, 7, 14, 15, 20, tzinfo=SHANGHAI)
INTRADAY_TIME = datetime(2026, 7, 14, 10, 1, tzinfo=SHANGHAI)


def authenticated_client(
    tmp_path,
    *,
    raise_server_exceptions: bool = True,
    enable_market_fetch: bool = False,
    market_provider: str = "akshare",
) -> tuple[TestClient, dict[str, str], Settings]:
    settings = Settings(
        database_path=tmp_path / "workflow-api.db",
        enable_market_fetch=enable_market_fetch,
        market_provider=market_provider,
    )
    client = TestClient(
        create_app(settings),
        raise_server_exceptions=raise_server_exceptions,
    )
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    return (
        client,
        {"Authorization": f"Bearer {login.json()['access_token']}"},
        settings,
    )


def install_clock(monkeypatch, value: datetime) -> None:
    import quantitative_trading.api.routes.service_workflows as routes

    monkeypatch.setattr(routes, "_current_time", lambda: value.astimezone(UTC))


def install_workflow(monkeypatch, workflow) -> list[datetime]:
    import quantitative_trading.api.routes.service_workflows as routes

    factory_times: list[datetime] = []

    def factory(connection, settings, *, now):
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        factory_times.append(now())
        return workflow

    monkeypatch.setattr(routes, "build_decision_workflow", factory)
    return factory_times


def seed_enabled_symbols(settings: Settings, *symbols: str) -> None:
    with connect(settings) as connection:
        InstrumentRepository(connection).replace_catalog(
            [
                InstrumentMetadata(
                    symbol=symbol,
                    name=symbol,
                    exchange=Exchange.SH if symbol.startswith("6") else Exchange.SZ,
                    instrument_type=InstrumentType.A_SHARE,
                    settlement_cycle=SettlementCycle.T1,
                    metadata_source="test-directory",
                    metadata_checked_at=INTRADAY_TIME,
                    rule_version="test-rules-v1",
                )
                for symbol in symbols
            ]
        )
        repository = WatchPinnedRepository(connection)
        for rank, symbol in enumerate(symbols, start=1):
            repository.upsert(
                WatchPinnedInput(
                    symbol=symbol,
                    name=symbol,
                    rank=rank,
                    plan_enabled=True,
                ),
                source=WatchPinnedSource.MANUAL,
                now=INTRADAY_TIME,
            )


def test_workflow_routes_require_authentication(tmp_path) -> None:
    client, _headers, _settings = authenticated_client(tmp_path)

    for workflow_type in ("close", "intraday", "backfill", "cleanup"):
        response = client.post(
            f"/api/v1/service/workflows/{workflow_type}/run", json={}
        )

        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


def test_workflow_route_rejects_unknown_type_and_cross_workflow_fields(
    tmp_path,
) -> None:
    client, headers, _settings = authenticated_client(tmp_path)

    unknown = client.post(
        "/api/v1/service/workflows/unknown/run", json={}, headers=headers
    )
    intraday_fields = client.post(
        "/api/v1/service/workflows/intraday/run",
        json={"trade_date": "2026-07-14"},
        headers=headers,
    )
    cleanup_fields = client.post(
        "/api/v1/service/workflows/cleanup/run",
        json={"force": True, "manual_reason": "not applicable"},
        headers=headers,
    )
    close_fields = client.post(
        "/api/v1/service/workflows/close/run",
        json={"as_of": "2026-07-14"},
        headers=headers,
    )
    backfill_fields = client.post(
        "/api/v1/service/workflows/backfill/run",
        json={"force": True, "manual_reason": "not applicable"},
        headers=headers,
    )

    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_error"
    for response in (intraday_fields, cleanup_fields, close_fields, backfill_fields):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "workflow_request_invalid"


def test_backfill_requires_enabled_akshare_provider(tmp_path, monkeypatch) -> None:
    install_clock(monkeypatch, INTRADAY_TIME)
    disabled_client, disabled_headers, _settings = authenticated_client(tmp_path)
    disabled = disabled_client.post(
        "/api/v1/service/workflows/backfill/run", json={}, headers=disabled_headers
    )

    other_client, other_headers, _settings = authenticated_client(
        tmp_path / "other",
        enable_market_fetch=True,
        market_provider="unsupported",
    )
    other = other_client.post(
        "/api/v1/service/workflows/backfill/run", json={}, headers=other_headers
    )

    enabled_client, enabled_headers, _settings = authenticated_client(
        tmp_path / "enabled",
        enable_market_fetch=True,
    )
    non_trading_day = enabled_client.post(
        "/api/v1/service/workflows/backfill/run",
        json={"trade_date": "2026-07-18"},
        headers=enabled_headers,
    )

    assert disabled.status_code == 422
    assert disabled.json()["error"]["code"] == "workflow_not_available"
    assert other.status_code == 422
    assert other.json()["error"]["code"] == "workflow_not_available"
    assert non_trading_day.status_code == 422
    assert non_trading_day.json()["error"]["code"] == "workflow_calendar_guard"


def test_backfill_defaults_date_and_decision_enabled_universe_without_network(
    tmp_path,
    monkeypatch,
) -> None:
    install_clock(monkeypatch, INTRADAY_TIME)
    client, headers, settings = authenticated_client(
        tmp_path,
        enable_market_fetch=True,
    )

    response = client.post(
        "/api/v1/service/workflows/backfill/run", json={}, headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task"] == "backfill"
    assert body["status"] == "success"
    assert body["run_id"].startswith("backfill-2026-07-14-")
    assert body["snapshot_id"] is None
    assert body["plan_id"] is None
    assert body["recommendation_ids"] == []
    assert body["warnings"] == [
        "empty decision-enabled universe; providers were not called"
    ]
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list(
            event_type="service.workflow.run_requested"
        )
    assert audits[0].payload["workflow_type"] == "backfill"
    assert audits[0].payload["trade_date"] == "2026-07-14"
    assert audits[0].payload["symbols"] is None


def test_backfill_passes_explicit_symbols_and_maps_failed_summary(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[tuple[date, list[str] | None]] = []

    class FakeWorkflow:
        def run_backfill(self, trade_date: date, *, symbols=None):
            calls.append((trade_date, symbols))
            return SimpleNamespace(
                run_id="backfill-failed",
                status=CaptureRunStatus.FAILED,
                reused=False,
                warnings=["all requested datasets failed"],
            )

    install_clock(monkeypatch, INTRADAY_TIME)
    install_workflow(monkeypatch, FakeWorkflow())
    client, headers, settings = authenticated_client(
        tmp_path,
        enable_market_fetch=True,
    )
    seed_enabled_symbols(settings, "600000", "000001")

    response = client.post(
        "/api/v1/service/workflows/backfill/run",
        json={
            "trade_date": "2026-07-13",
            "symbols": ["600000", "000001"],
        },
        headers=headers,
    )
    invalid_symbol = client.post(
        "/api/v1/service/workflows/backfill/run",
        json={"symbols": ["bad"]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["run_id"] == "backfill-failed"
    assert response.json()["warnings"] == ["all requested datasets failed"]
    assert calls == [(date(2026, 7, 13), ["600000", "000001"])]
    with connect(settings) as connection:
        alerts = NotificationRepository(connection).list_recent(limit=10)
    assert len(alerts) == 1
    assert alerts[0].action == "system_alert"
    assert invalid_symbol.status_code == 422
    assert invalid_symbol.json()["error"]["code"] == "validation_error"


def test_backfill_latest_complete_resolves_cutoff_and_validates_scope(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[tuple[date, list[str] | None]] = []

    class FakeWorkflow:
        def run_backfill(self, trade_date: date, *, symbols=None):
            calls.append((trade_date, symbols))
            return SimpleNamespace(
                run_id="backfill-latest-complete",
                status=CaptureRunStatus.SUCCEEDED,
                reused=False,
                warnings=(),
            )

    install_clock(monkeypatch, INTRADAY_TIME)
    install_workflow(monkeypatch, FakeWorkflow())
    client, headers, settings = authenticated_client(
        tmp_path,
        enable_market_fetch=True,
    )
    with connect(settings) as connection:
        PositionRepository(connection).add(
            PositionInput(
                symbol="512480",
                name="半导体ETF",
                quantity=1000,
                available_quantity=1000,
                cost_price=1.0,
                opened_at=INTRADAY_TIME.date(),
            ),
            now=INTRADAY_TIME,
        )
        WatchPinnedRepository(connection).upsert(
            WatchPinnedInput(
                symbol="000001",
                name="disabled watch",
                rank=1,
                plan_enabled=False,
            ),
            source=WatchPinnedSource.MANUAL,
            now=INTRADAY_TIME,
        )

    response = client.post(
        "/api/v1/service/workflows/backfill/run",
        json={"as_of_mode": "latest_complete", "symbols": ["512480"]},
        headers=headers,
    )
    conflicting = client.post(
        "/api/v1/service/workflows/backfill/run",
        json={
            "as_of_mode": "latest_complete",
            "trade_date": "2026-07-13",
        },
        headers=headers,
    )
    outside_scope = client.post(
        "/api/v1/service/workflows/backfill/run",
        json={"as_of_mode": "latest_complete", "symbols": ["000001"]},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == "backfill-latest-complete"
    assert calls == [(date(2026, 7, 13), ["512480"])]
    assert conflicting.status_code == 422
    assert conflicting.json()["error"]["code"] == "workflow_request_invalid"
    assert outside_scope.status_code == 422
    assert outside_scope.json()["error"]["code"] == "workflow_request_invalid"
    with connect(settings) as connection:
        requested_audits = AuditLogRepository(connection).list(
            event_type="service.workflow.run_requested"
        )
        rejected_audits = AuditLogRepository(connection).list(
            event_type="service.workflow.run_rejected"
        )
    assert requested_audits[0].payload["as_of_mode"] == "latest_complete"
    assert len(rejected_audits) == 2
    assert all(
        audit.payload["as_of_mode"] == "latest_complete"
        for audit in rejected_audits
    )


def test_close_workflow_uses_factory_and_returns_unified_result(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[tuple[date, bool]] = []

    class FakeWorkflow:
        def run_close(self, trade_date: date, *, skip_calendar: bool = False):
            calls.append((trade_date, skip_calendar))
            return SimpleNamespace(
                run_id="close-20260714",
                ready=True,
                reused=False,
                market_input_snapshot_id=17,
                plan_id="plan-20260715",
                warnings=("money flow degraded",),
            )

    install_clock(monkeypatch, NORMAL_CLOSE_TIME)
    factory_times = install_workflow(monkeypatch, FakeWorkflow())
    client, headers, _settings = authenticated_client(tmp_path)

    response = client.post(
        "/api/v1/service/workflows/close/run", json={}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "task": "close",
        "status": "success",
        "run_id": "close-20260714",
        "snapshot_id": 17,
        "plan_id": "plan-20260715",
        "recommendation_ids": [],
        "warnings": ["money flow degraded"],
        "reused": False,
        "ready": True,
        "cleaned_rows": None,
    }
    assert calls == [(date(2026, 7, 14), False)]
    assert factory_times == [NORMAL_CLOSE_TIME.astimezone(UTC)]


def test_close_late_run_requires_reason_and_writes_sanitized_audit(
    tmp_path,
    monkeypatch,
) -> None:
    late_time = datetime(2026, 7, 14, 17, 0, tzinfo=SHANGHAI)

    class FakeWorkflow:
        def run_close(self, trade_date: date, *, skip_calendar: bool = False):
            return SimpleNamespace(
                run_id="close-20260714",
                ready=False,
                reused=False,
                market_input_snapshot_id=18,
                plan_id=None,
                warnings=("daily data not ready",),
            )

    install_clock(monkeypatch, late_time)
    install_workflow(monkeypatch, FakeWorkflow())
    client, headers, settings = authenticated_client(tmp_path)

    missing_reason = client.post(
        "/api/v1/service/workflows/close/run", json={}, headers=headers
    )
    response = client.post(
        "/api/v1/service/workflows/close/run",
        json={"manual_reason": "verified late market data"},
        headers=headers,
    )

    assert missing_reason.status_code == 422
    assert missing_reason.json()["error"]["code"] == "manual_reason_required"
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["ready"] is False
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list(
            event_type="service.workflow.run_requested"
        )
        rejected = AuditLogRepository(connection).list(
            event_type="service.workflow.run_rejected"
        )
    assert len(audits) == 1
    assert audits[0].payload == {
        "workflow_type": "close",
        "trade_date": "2026-07-14",
        "as_of": None,
        "force": False,
        "skip_calendar": False,
        "late": True,
        "manual_reason": "verified late market data",
        "symbols": None,
    }
    assert len(rejected) == 1
    assert rejected[0].payload["error_code"] == "manual_reason_required"
    assert rejected[0].payload["workflow_type"] == "close"


def test_close_skip_calendar_and_force_require_reason_and_reach_workflow(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[tuple[date, bool]] = []

    class FakeWorkflow:
        def run_close(self, trade_date: date, *, skip_calendar: bool = False):
            calls.append((trade_date, skip_calendar))
            return SimpleNamespace(
                run_id=f"close-{trade_date:%Y%m%d}",
                ready=True,
                reused=True,
                market_input_snapshot_id=19,
                plan_id="plan-manual",
                warnings=(),
            )

    install_clock(
        monkeypatch,
        datetime(2026, 7, 18, 15, 20, tzinfo=SHANGHAI),
    )
    install_workflow(monkeypatch, FakeWorkflow())
    client, headers, _settings = authenticated_client(tmp_path)

    missing_skip_reason = client.post(
        "/api/v1/service/workflows/close/run",
        json={"skip_calendar": True},
        headers=headers,
    )
    skipped = client.post(
        "/api/v1/service/workflows/close/run",
        json={"skip_calendar": True, "manual_reason": "exchange calendar override"},
        headers=headers,
    )

    install_clock(
        monkeypatch,
        datetime(2026, 7, 14, 14, 0, tzinfo=SHANGHAI),
    )
    missing_force_reason = client.post(
        "/api/v1/service/workflows/close/run",
        json={"force": True},
        headers=headers,
    )
    forced = client.post(
        "/api/v1/service/workflows/close/run",
        json={"force": True, "manual_reason": "operator verified early data"},
        headers=headers,
    )

    assert missing_skip_reason.status_code == 422
    assert missing_skip_reason.json()["error"]["code"] == "manual_reason_required"
    assert skipped.status_code == 200
    assert missing_force_reason.status_code == 422
    assert missing_force_reason.json()["error"]["code"] == "manual_reason_required"
    assert forced.status_code == 200
    assert calls == [
        (date(2026, 7, 18), True),
        (date(2026, 7, 14), False),
    ]


def test_close_skip_calendar_integrates_real_decision_workflow(
    tmp_path,
    monkeypatch,
) -> None:
    saturday = datetime(2026, 7, 18, 15, 20, tzinfo=SHANGHAI)
    install_clock(monkeypatch, saturday)
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        CashAccountRepository(connection).initialize(
            50_000,
            now=saturday.astimezone(UTC),
            note="test principal",
        )

    response = client.post(
        "/api/v1/service/workflows/close/run",
        json={
            "skip_calendar": True,
            "manual_reason": "verified exceptional market session",
        },
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task"] == "close"
    assert body["run_id"] == "close-20260718"
    assert body["snapshot_id"] > 0
    assert body["plan_id"] is None
    assert body["recommendation_ids"] == []
    assert body["warnings"] == ["决策启用集合为空"]


def test_close_outside_window_and_non_trading_day_need_explicit_override(
    tmp_path,
    monkeypatch,
) -> None:
    install_workflow(
        monkeypatch,
        SimpleNamespace(
            run_close=lambda *args, **kwargs: pytest.fail("workflow must not run")
        ),
    )
    client, headers, _settings = authenticated_client(tmp_path)

    install_clock(
        monkeypatch,
        datetime(2026, 7, 14, 14, 0, tzinfo=SHANGHAI),
    )
    early = client.post("/api/v1/service/workflows/close/run", json={}, headers=headers)
    install_clock(
        monkeypatch,
        datetime(2026, 7, 18, 15, 20, tzinfo=SHANGHAI),
    )
    weekend = client.post(
        "/api/v1/service/workflows/close/run",
        json={"manual_reason": "reason without override"},
        headers=headers,
    )

    assert early.status_code == 422
    assert early.json()["error"]["code"] == "workflow_outside_window"
    assert weekend.status_code == 422
    assert weekend.json()["error"]["code"] == "workflow_calendar_guard"


def test_intraday_uses_current_period_and_rejects_outside_trading_session(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[str] = []

    class FakeWorkflow:
        def run_intraday(self):
            calls.append("intraday")
            return SimpleNamespace(
                run_id="intraday-20260714-1000",
                reused=False,
                status=CaptureRunStatus.DEGRADED,
                market_input_snapshot_id=23,
                recommendation_ids=("rec-1", "rec-2"),
                warnings=("quote partial",),
            )

    install_clock(monkeypatch, INTRADAY_TIME)
    install_workflow(monkeypatch, FakeWorkflow())
    client, headers, _settings = authenticated_client(tmp_path)

    response = client.post("/api/v1/service/workflows/intraday/run", headers=headers)
    install_clock(
        monkeypatch,
        datetime(2026, 7, 14, 12, 0, tzinfo=SHANGHAI),
    )
    outside = client.post(
        "/api/v1/service/workflows/intraday/run", json={}, headers=headers
    )

    assert response.status_code == 200
    assert response.json() == {
        "task": "intraday",
        "status": "degraded",
        "run_id": "intraday-20260714-1000",
        "snapshot_id": 23,
        "plan_id": None,
        "recommendation_ids": ["rec-1", "rec-2"],
        "warnings": ["quote partial"],
        "reused": False,
        "ready": None,
        "cleaned_rows": None,
    }
    assert outside.status_code == 422
    assert outside.json()["error"]["code"] == "workflow_outside_session"
    assert calls == ["intraday"]


def test_intraday_failed_result_dispatches_system_alert(tmp_path, monkeypatch) -> None:
    class FailedWorkflow:
        def run_intraday(self):
            return SimpleNamespace(
                run_id="intraday-20260714-1000",
                reused=False,
                status=CaptureRunStatus.FAILED,
                market_input_snapshot_id=24,
                recommendation_ids=(),
                warnings=("all requested quotes unavailable",),
            )

    install_clock(monkeypatch, INTRADAY_TIME)
    install_workflow(monkeypatch, FailedWorkflow())
    client, headers, settings = authenticated_client(tmp_path)

    response = client.post("/api/v1/service/workflows/intraday/run", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    with connect(settings) as connection:
        alerts = NotificationRepository(connection).list_recent(limit=10)
    assert len(alerts) == 1
    assert alerts[0].action == "system_alert"
    assert alerts[0].reason[0].startswith("intraday workflow failed:")


def test_cleanup_uses_retention_service_and_defaults_as_of(
    tmp_path,
    monkeypatch,
) -> None:
    cleanup_dates: list[date] = []

    class FakeWorkflow:
        def run_cleanup(self, as_of: date):
            cleanup_dates.append(as_of)
            return SimpleNamespace(
                run_id=f"cleanup-{as_of.isoformat()}",
                reused=False,
                cleaned_rows=31,
                warnings=(),
            )

    install_clock(
        monkeypatch,
        datetime(2026, 7, 14, 16, 35, tzinfo=SHANGHAI),
    )
    install_workflow(monkeypatch, FakeWorkflow())
    client, headers, _settings = authenticated_client(tmp_path)

    defaulted = client.post("/api/v1/service/workflows/cleanup/run", headers=headers)
    explicit = client.post(
        "/api/v1/service/workflows/cleanup/run",
        json={"as_of": "2026-07-13"},
        headers=headers,
    )

    assert defaulted.status_code == 200
    assert defaulted.json() == {
        "task": "cleanup",
        "status": "success",
        "run_id": "cleanup-2026-07-14",
        "snapshot_id": None,
        "plan_id": None,
        "recommendation_ids": [],
        "warnings": [],
        "reused": False,
        "ready": None,
        "cleaned_rows": 31,
    }
    assert explicit.status_code == 200
    assert explicit.json()["run_id"] == "cleanup-2026-07-13"
    assert cleanup_dates == [date(2026, 7, 14), date(2026, 7, 13)]


def test_workflow_failures_return_stable_sanitized_error(
    tmp_path,
    monkeypatch,
) -> None:
    class FailingWorkflow:
        def run_intraday(self):
            raise RuntimeError(
                "provider token=supersecret Authorization: Bearer abc "
                "database=/tmp/private/workflow.db"
            )

    install_clock(monkeypatch, INTRADAY_TIME)
    install_workflow(monkeypatch, FailingWorkflow())
    client, headers, settings = authenticated_client(
        tmp_path,
        raise_server_exceptions=False,
    )

    response = client.post("/api/v1/service/workflows/intraday/run", headers=headers)

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "workflow_run_failed"
    assert "supersecret" not in response.text
    assert "Bearer abc" not in response.text
    assert "/tmp/private" not in response.text
    with connect(settings) as connection:
        failed = AuditLogRepository(connection).list(
            event_type="service.workflow.run_failed"
        )
        alerts = NotificationRepository(connection).list_recent(limit=10)
    assert len(failed) == 1
    assert failed[0].payload["error_code"] == "workflow_run_failed"
    assert len(alerts) == 1
    assert alerts[0].action == "system_alert"
    assert "supersecret" not in alerts[0].model_dump_json()
    assert "/tmp/private" not in alerts[0].model_dump_json()


def test_concurrent_workflow_returns_stable_conflict(tmp_path, monkeypatch) -> None:
    class ActiveWorkflow:
        def run_intraday(self):
            raise WorkflowAlreadyRunningError("intraday-20260714-1000")

    install_clock(monkeypatch, INTRADAY_TIME)
    install_workflow(monkeypatch, ActiveWorkflow())
    client, headers, _settings = authenticated_client(tmp_path)

    response = client.post("/api/v1/service/workflows/intraday/run", headers=headers)

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "workflow_in_progress",
        "message": "workflow run is already in progress",
        "details": {"run_id": "intraday-20260714-1000"},
    }
