from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from quantitative_trading.api.app import create_app
from quantitative_trading.audit.models import AuditLog
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    CaptureRunStatus,
    DailyBar,
    DailyMoneyFlow,
    HistorySnapshot,
    IntradayStrengthSnapshot,
    MarketCaptureResult,
    MarketCaptureRun,
    MarketInputSnapshot,
    MinuteBar,
    MoneyFlowSnapshot,
    QuoteSnapshot,
    QuoteStatus,
    StrengthComponent,
    StrengthConfidence,
    StrengthLabel,
    ComponentStatus,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    IntradayStrengthSnapshotRepository,
    MarketCaptureResultRepository,
    MarketCaptureRunRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
    MinuteBarRepository,
)
from quantitative_trading.market.repository import (
    MarketInputSnapshotRepository,
    QuoteSnapshotRepository,
)
from quantitative_trading.notification.models import NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.planning.models import TradingPlan, TradingPlanStatus
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.recommendation.models import (
    Recommendation,
    RecommendationAction,
)
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect
from quantitative_trading.universe.models import (
    UniverseMember,
    UniverseSnapshot,
    UniverseSnapshotStatus,
    UniverseSource,
)
from quantitative_trading.universe.repository import UniverseSnapshotRepository


SHANGHAI = timezone(timedelta(hours=8))
FETCHED_AT = datetime(2026, 7, 13, 2, 3, tzinfo=UTC)
DATA_TIME = datetime(2026, 7, 13, 10, 2, tzinfo=SHANGHAI)


def authenticated_client(tmp_path) -> tuple[TestClient, dict[str, str], Settings]:
    settings = Settings(database_path=tmp_path / "api.db", enable_market_fetch=False)
    client = TestClient(create_app(settings))
    client.post("/api/v1/auth/setup-password", json={"password": "local-password"})
    login = client.post("/api/v1/auth/login", json={"password": "local-password"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return client, headers, settings


def _universe_member(
    symbol: str,
    name: str,
    *,
    priority: int,
    source: UniverseSource,
) -> UniverseMember:
    return UniverseMember(
        symbol=symbol,
        name=name,
        sources=[source],
        priority=priority,
        ledger_updated_at=FETCHED_AT if source is UniverseSource.HOLDING else None,
        watch_pinned_rank=priority if source is UniverseSource.WATCH_PINNED else None,
        plan_enabled=True,
        plan_enabled_source=source,
        created_at=FETCHED_AT,
    )


def seed_market_data(settings: Settings) -> int:
    with connect(settings) as connection:
        universe_id = UniverseSnapshotRepository(connection).save(
            UniverseSnapshot(
                created_at=FETCHED_AT,
                status=UniverseSnapshotStatus.OK,
                warnings=[],
                members=[
                    _universe_member(
                        "600000", "Pufa Bank", priority=0, source=UniverseSource.HOLDING
                    ),
                    _universe_member(
                        "000001",
                        "Ping An Bank",
                        priority=1,
                        source=UniverseSource.WATCH_PINNED,
                    ),
                ],
            )
        )
        quote_id = QuoteSnapshotRepository(connection).save(
            QuoteSnapshot(
                symbol="600000",
                name="Pufa Bank",
                previous_close=14,
                current_price=15,
                change_pct=7.14,
                data_time=DATA_TIME,
                fetched_at=FETCHED_AT,
                source="akshare",
                status=QuoteStatus.OK,
            )
        )

        run = MarketCaptureRun(
            run_id="run-close-20260713",
            workflow_type="close",
            trade_date=date(2026, 7, 13),
            idempotency_key="close:2026-07-13",
            status=CaptureRunStatus.SUCCEEDED,
            started_at=datetime(2026, 7, 13, 1, 59, tzinfo=UTC),
            finished_at=FETCHED_AT,
            requested_symbols=2,
            processed_symbols=2,
            provider_calls=4,
            provider_duration_ms=87.25,
            rows_received=9,
            rows_written=9,
            plan_count=1,
            notification_count=2,
            email_outbox_count=1,
            retry_count=1,
        )
        MarketCaptureRunRepository(connection).get_or_create(run)

        trade_dates = [
            date(2026, 7, 6),
            date(2026, 7, 7),
            date(2026, 7, 8),
            date(2026, 7, 9),
            date(2026, 7, 10),
            date(2026, 7, 13),
        ]
        daily_repository = DailyBarRepository(connection)
        daily_ids = []
        for index, trade_date in enumerate(trade_dates):
            close = float(10 + index)
            daily_ids.append(
                daily_repository.save(
                    DailyBar(
                        symbol="600000",
                        trade_date=trade_date,
                        open=close - 0.2,
                        high=close + 0.4,
                        low=close - 0.5,
                        close=close,
                        volume=100 + index,
                        amount=(100 + index) * close,
                        source="akshare",
                        source_updated_at=FETCHED_AT,
                        fetched_at=FETCHED_AT,
                    )
                )
            )
        history_id = HistorySnapshotRepository(connection).save(
            HistorySnapshot(
                run_id=run.run_id,
                symbol="600000",
                data_start=trade_dates[0],
                data_end=trade_dates[-1],
                row_count=len(daily_ids),
                content_digest="a" * 64,
                status=CaptureResultStatus.COMPLETE,
                fetched_at=FETCHED_AT,
            ),
            daily_ids,
        )

        flow = DailyMoneyFlow(
            symbol="600000",
            trade_date=date(2026, 7, 13),
            main_net_amount=1000,
            main_net_pct=1.1,
            super_large_net_amount=600,
            super_large_net_pct=0.6,
            large_net_amount=400,
            large_net_pct=0.4,
            medium_net_amount=-300,
            medium_net_pct=-0.3,
            small_net_amount=-700,
            small_net_pct=-0.7,
            source="akshare",
            source_updated_at=FETCHED_AT,
            fetched_at=FETCHED_AT,
        )
        flow_id = MoneyFlowRepository(connection).save(flow)
        money_snapshot_id = MoneyFlowSnapshotRepository(connection).save(
            MoneyFlowSnapshot(
                run_id=run.run_id,
                symbol="600000",
                data_start=flow.trade_date,
                data_end=flow.trade_date,
                row_count=1,
                content_digest="b" * 64,
                status=CaptureResultStatus.COMPLETE,
                fetched_at=FETCHED_AT,
            ),
            [flow_id],
        )

        minute_bars = [
            MinuteBar(
                symbol="600000",
                trade_date=date(2026, 7, 13),
                minute=datetime(2026, 7, 13, 10, minute, tzinfo=SHANGHAI),
                open=14.8 + minute / 10,
                high=15.2,
                low=14.7,
                close=14.9 + minute / 10,
                volume=100 if minute == 0 else 200,
                amount=1000 if minute == 0 else 2200,
                source="akshare",
                fetched_at=FETCHED_AT,
            )
            for minute in (0, 2)
        ]
        MinuteBarRepository(connection).upsert_many(minute_bars)

        strength = IntradayStrengthSnapshot(
            run_id=run.run_id,
            symbol="600000",
            trade_date=date(2026, 7, 13),
            label=StrengthLabel.STRONG,
            confidence=StrengthConfidence.MEDIUM,
            degraded=False,
            degradation_reasons=[],
            components=[
                StrengthComponent(
                    name="vwap_position",
                    status=ComponentStatus.AVAILABLE,
                    value=0.2,
                    threshold=0.1,
                    direction=1,
                    reason="price above cumulative VWAP",
                    source="derived",
                )
            ],
            direction_sum=1,
            thresholds={"vwap_pct": 0.1},
            rule_version="intraday-strength-v1",
            last_minute=minute_bars[-1].minute,
            data_coverage=1,
            source="derived",
            data_time=minute_bars[-1].minute,
            fetched_at=FETCHED_AT,
        )
        strength_id = IntradayStrengthSnapshotRepository(connection).save(strength)

        MarketCaptureResultRepository(connection).upsert(
            MarketCaptureResult(
                run_id=run.run_id,
                symbol="600000",
                dataset=CaptureDataset.DAILY_BAR,
                status=CaptureResultStatus.COMPLETE,
                data_start=trade_dates[0],
                data_end=trade_dates[-1],
                fetched_at=FETCHED_AT,
                expected_rows=6,
                actual_rows=6,
                source="akshare",
            )
        )

        market_snapshot_id = MarketInputSnapshotRepository(connection).save(
            MarketInputSnapshot(
                universe_snapshot_id=universe_id,
                quote_snapshot_refs={"600000": quote_id},
                history_snapshot_refs={"600000": history_id},
                money_flow_snapshot_refs={"600000": money_snapshot_id},
                intraday_strength_snapshot_refs={"600000": strength_id},
                capture_run_id=run.run_id,
                thresholds={"stale_trading_minutes": 6},
                data_time=DATA_TIME,
                fetched_at=FETCHED_AT,
                warnings=[],
            )
        )
        PositionRepository(connection).add(
            PositionInput(
                symbol="600000",
                name="Pufa Bank",
                quantity=1000,
                available_quantity=800,
                cost_price=10,
                opened_at=date(2026, 7, 1),
            ),
            now=FETCHED_AT,
        )
        plan = TradingPlan(
            plan_id="plan-20260713",
            trading_day=date(2026, 7, 13),
            generated_at=datetime(2026, 7, 12, 8, tzinfo=UTC),
            valid_until=datetime(2026, 7, 13, 15, tzinfo=SHANGHAI),
            universe_snapshot_id=universe_id,
            account_snapshot_id=None,
            ledger_max_updated_at=FETCHED_AT,
            watch_symbols=[],
            holding_symbols=["600000"],
            key_levels={"600000": {"support": 13.5, "resistance": 15.5}},
            candidate_actions={"600000": ["hold", "reduce"]},
            invalid_if={"600000": ["price falls below 13.5"]},
            warnings=[],
            status=TradingPlanStatus.ACTIVE,
            source_run_id=run.run_id,
            market_input_snapshot_id=market_snapshot_id,
            data_time=DATA_TIME,
            data_quality="complete",
        )
        TradingPlanRepository(connection).save(plan)
        recommendation = Recommendation(
            recommendation_id="rec-600000-1",
            symbol="600000",
            name="Pufa Bank",
            action=RecommendationAction.HOLD,
            confidence="medium",
            position_context={},
            account_context={},
            price_context={"current_price": 15},
            reason=["price remains above plan support"],
            risk={"invalid_if": ["price falls below 13.5"]},
            valid_until=plan.valid_until,
            data_time=datetime(2026, 7, 13, 10, 1, tzinfo=SHANGHAI),
            created_at=FETCHED_AT,
            run_id=run.run_id,
            market_input_snapshot_id=market_snapshot_id,
            plan_id=plan.plan_id,
            audit_id="audit-1",
        )
        RecommendationRepository(connection).save_many(
            [recommendation], created_at=FETCHED_AT
        )
        AuditLogRepository(connection).save(
            AuditLog(
                audit_id="audit-1",
                event_type="recommendation.generated",
                recommendation_id=recommendation.recommendation_id,
                payload={"symbol": "600000", "action": "hold"},
                created_at=FETCHED_AT,
            )
        )
        NotificationRepository(connection).save(
            NotificationSummary(
                notification_id="notification-1",
                recommendation_id=recommendation.recommendation_id,
                symbol="600000",
                action="hold",
                confidence="medium",
                key_price=15,
                reason=["price remains above plan support"],
                risk=["price falls below 13.5"],
                data_time=recommendation.data_time,
                audit_id="audit-1",
                created_at=FETCHED_AT,
            )
        )
        return market_snapshot_id


def test_market_read_routes_require_authentication(tmp_path) -> None:
    client, _headers, _settings = authenticated_client(tmp_path)

    paths = [
        "/api/v1/market/symbols",
        "/api/v1/market/symbols/600000/overview",
        "/api/v1/market/symbols/600000/daily-bars",
        "/api/v1/market/symbols/600000/money-flow",
        "/api/v1/market/symbols/600000/minute-bars",
        "/api/v1/market/symbols/600000/intraday-strength/latest",
        "/api/v1/market/runs",
        "/api/v1/market/runs/missing",
        "/api/v1/market/snapshots/1/trace?symbol=600000",
    ]

    for path in paths:
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "unauthorized"


def test_market_read_validation_rejects_bad_symbols_windows_and_pagination(
    tmp_path,
) -> None:
    client, headers, _settings = authenticated_client(tmp_path)

    paths = [
        "/api/v1/market/symbols/not-a-symbol/overview",
        "/api/v1/market/symbols/600000/daily-bars?limit=251",
        "/api/v1/market/symbols/600000/money-flow?limit=61",
        "/api/v1/market/symbols/600000/minute-bars?trade_date=2026-99-99",
        "/api/v1/market/symbols?page=0",
        "/api/v1/market/runs?page_size=0",
        "/api/v1/market/snapshots/1/trace",
        "/api/v1/market/snapshots/1/trace?symbol=bad",
    ]

    for path in paths:
        response = client.get(path, headers=headers)
        assert response.status_code == 422, path
        assert response.json()["error"]["code"] == "validation_error"


def test_empty_market_datasets_return_explicit_unavailable_responses(tmp_path) -> None:
    client, headers, _settings = authenticated_client(tmp_path)

    symbols = client.get("/api/v1/market/symbols", headers=headers)
    daily = client.get("/api/v1/market/symbols/600000/daily-bars", headers=headers)
    flow = client.get("/api/v1/market/symbols/600000/money-flow", headers=headers)
    minute = client.get("/api/v1/market/symbols/600000/minute-bars", headers=headers)
    strength = client.get(
        "/api/v1/market/symbols/600000/intraday-strength/latest", headers=headers
    )
    overview = client.get("/api/v1/market/symbols/600000/overview", headers=headers)

    assert symbols.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}
    assert (
        daily.status_code
        == flow.status_code
        == minute.status_code
        == strength.status_code
        == 200
    )
    assert daily.json()["status"] == "unavailable"
    assert daily.json()["data_time"] is None
    assert daily.json()["bars"] == []
    assert daily.json()["warnings"]
    assert flow.json()["status"] == "unavailable"
    assert flow.json()["rows"] == []
    assert minute.json()["status"] == "unavailable"
    assert minute.json()["bars"] == []
    assert strength.json()["status"] == "unavailable"
    assert strength.json()["components"] == []
    assert overview.status_code == 200
    assert overview.json()["status"] == "unavailable"
    assert overview.json()["data_time"] is None
    assert overview.json()["warnings"]


def test_symbol_scanner_paginates_latest_universe_and_composes_state(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    seed_market_data(settings)

    first = client.get("/api/v1/market/symbols?page=1&page_size=1", headers=headers)
    second = client.get("/api/v1/market/symbols?page=2&page_size=1", headers=headers)

    assert first.status_code == 200
    assert first.json()["total"] == 2
    assert first.json()["page"] == 1
    assert first.json()["page_size"] == 1
    assert first.json()["items"][0] == {
        "symbol": "600000",
        "name": "Pufa Bank",
        "sources": ["holding"],
        "current_price": 15.0,
        "change_pct": 7.14,
        "recommendation_action": "hold",
        "intraday_strength": "strong",
        "plan_status": "active",
        "quality_status": "complete",
        "unread_count": 1,
        "data_time": DATA_TIME.isoformat(),
        "warnings": [],
    }
    assert second.json()["items"][0]["symbol"] == "000001"
    assert second.json()["items"][0]["quality_status"] == "unavailable"


def test_overview_combines_manual_position_plan_recommendation_and_features(
    tmp_path,
) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    snapshot_id = seed_market_data(settings)

    response = client.get("/api/v1/market/symbols/600000/overview", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == snapshot_id
    assert body["status"] == "complete"
    assert body["position"] == {
        "quantity": 1000,
        "available_quantity": 800,
        "cost_price": 10.0,
        "floating_pnl_pct": 0.5,
    }
    assert body["plan"]["allowed_actions"] == ["hold", "reduce"]
    assert body["recommendation"]["recommendation_id"] == "rec-600000-1"
    assert body["market_structure"]["support"] is not None
    assert body["intraday_strength"]["label"] == "strong"
    assert "price falls below 13.5" in body["risks"]


def test_daily_bars_include_rolling_averages_with_pre_limit_context(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    seed_market_data(settings)

    response = client.get(
        "/api/v1/market/symbols/600000/daily-bars?limit=2", headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert [bar["trade_date"] for bar in body["bars"]] == ["2026-07-10", "2026-07-13"]
    assert body["bars"][0]["ma5"] == pytest.approx(12)
    assert body["bars"][1]["ma5"] == pytest.approx(13)
    assert body["bars"][0]["ma10"] is None
    assert datetime.fromisoformat(body["data_time"]) == FETCHED_AT


def test_daily_and_money_flow_windows_degrade_when_requested_coverage_is_short(
    tmp_path,
) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    seed_market_data(settings)

    daily = client.get(
        "/api/v1/market/symbols/600000/daily-bars?limit=10", headers=headers
    )
    flow = client.get(
        "/api/v1/market/symbols/600000/money-flow?limit=2", headers=headers
    )

    assert daily.status_code == flow.status_code == 200
    assert daily.json()["status"] == "degraded"
    assert "requested 10 daily bars, found 6" in daily.json()["warnings"]
    assert flow.json()["status"] == "degraded"
    assert "requested 2 money-flow rows, found 1" in flow.json()["warnings"]


def test_daily_window_degrades_when_trading_date_range_has_a_gap(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    with connect(settings) as connection:
        repository = DailyBarRepository(connection)
        for trade_date in (date(2026, 7, 9), date(2026, 7, 13)):
            repository.save(
                DailyBar(
                    symbol="000001",
                    trade_date=trade_date,
                    open=10,
                    high=10.5,
                    low=9.5,
                    close=10,
                    volume=100,
                    amount=1000,
                    source="akshare",
                    fetched_at=FETCHED_AT,
                )
            )

    response = client.get(
        "/api/v1/market/symbols/000001/daily-bars?limit=2", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert (
        "daily bar range is missing 1 XSHG trading day" in response.json()["warnings"]
    )


def test_money_flow_returns_all_five_normalized_levels(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    seed_market_data(settings)

    response = client.get(
        "/api/v1/market/symbols/600000/money-flow?limit=1", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["rows"] == [
        {
            "trade_date": "2026-07-13",
            "main_net_amount": 1000.0,
            "main_net_ratio": 1.1,
            "super_large_net_amount": 600.0,
            "super_large_net_ratio": 0.6,
            "large_net_amount": 400.0,
            "large_net_ratio": 0.4,
            "medium_net_amount": -300.0,
            "medium_net_ratio": -0.3,
            "small_net_amount": -700.0,
            "small_net_ratio": -0.7,
        }
    ]


def test_minute_bars_select_latest_date_and_add_vwap_previous_close_and_markers(
    tmp_path,
) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    seed_market_data(settings)

    response = client.get("/api/v1/market/symbols/600000/minute-bars", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["trade_date"] == "2026-07-13"
    assert body["previous_close"] == 14
    assert body["bars"][0]["vwap"] == pytest.approx(10)
    assert body["bars"][1]["vwap"] == pytest.approx(3200 / 300)
    assert body["recommendation_markers"] == [
        {
            "time": "10:01",
            "action": "hold",
            "price": 15.0,
            "recommendation_id": "rec-600000-1",
        }
    ]


def test_latest_intraday_strength_maps_components_and_quality(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    seed_market_data(settings)

    response = client.get(
        "/api/v1/market/symbols/600000/intraday-strength/latest", headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert body["label"] == "strong"
    assert body["confidence"] == "medium"
    assert body["coverage_ratio"] == 1
    assert body["last_minute"] == "10:02"
    assert body["degraded_reason"] is None
    assert body["components"][0]["key"] == "vwap_position"
    assert body["components"][0]["status"] == "complete"


def test_market_run_list_detail_results_and_missing_error(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    seed_market_data(settings)

    listed = client.get("/api/v1/market/runs?page=1&page_size=10", headers=headers)
    detail = client.get("/api/v1/market/runs/run-close-20260713", headers=headers)
    missing = client.get("/api/v1/market/runs/missing", headers=headers)

    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["run_id"] == "run-close-20260713"
    assert listed.json()["items"][0]["dataset_counts"]["daily_bar"] == {
        "complete": 1,
        "degraded": 0,
        "failed": 0,
        "stale": 0,
    }
    assert detail.status_code == 200
    assert detail.json()["run_id"] == "run-close-20260713"
    assert detail.json()["duration_ms"] == 240_000
    assert detail.json()["provider_duration_ms"] == 87.25
    assert detail.json()["plan_count"] == 1
    assert detail.json()["notification_count"] == 2
    assert detail.json()["email_outbox_count"] == 1
    assert detail.json()["retry_count"] == 1
    assert detail.json()["dataset_counts"]["daily_bar"]["complete"] == 1
    assert detail.json()["results"][0]["dataset"] == "daily_bar"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "market_run_not_found"


def test_market_snapshot_trace_resolves_all_dataset_and_decision_references(
    tmp_path,
) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    snapshot_id = seed_market_data(settings)

    response = client.get(
        f"/api/v1/market/snapshots/{snapshot_id}/trace?symbol=600000",
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "600000"
    assert body["run_id"] == "run-close-20260713"
    assert body["snapshot_id"] == snapshot_id
    assert body["plan_id"] == "plan-20260713"
    assert body["recommendation_id"] == "rec-600000-1"
    assert body["audit_id"] == "audit-1"
    assert body["thresholds"] == {"stale_trading_minutes": 6.0}
    assert [dataset["dataset"] for dataset in body["datasets"]] == [
        "quote",
        "history",
        "money_flow",
        "intraday_strength",
    ]
    assert all(dataset["reference_id"] is not None for dataset in body["datasets"])
    assert all(dataset["status"] == "complete" for dataset in body["datasets"])


def test_strength_trace_keeps_stable_range_after_raw_minute_cleanup(tmp_path) -> None:
    client, headers, settings = authenticated_client(tmp_path)
    snapshot_id = seed_market_data(settings)
    with connect(settings) as connection:
        connection.execute("DELETE FROM minute_bars WHERE symbol = ?", ("600000",))
        connection.commit()

    response = client.get(
        f"/api/v1/market/snapshots/{snapshot_id}/trace?symbol=600000",
        headers=headers,
    )

    assert response.status_code == 200
    strength = next(
        item
        for item in response.json()["datasets"]
        if item["dataset"] == "intraday_strength"
    )
    assert strength["status"] == "complete"
    assert strength["data_start"] == "2026-07-13T09:30:00+08:00"
    assert strength["data_end"] == "2026-07-13T10:02:00+08:00"


def test_market_snapshot_trace_preserves_snapshot_not_found_error(tmp_path) -> None:
    client, headers, _settings = authenticated_client(tmp_path)

    response = client.get(
        "/api/v1/market/snapshots/999/trace?symbol=600000", headers=headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "market_snapshot_not_found"
