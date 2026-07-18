from __future__ import annotations

import argparse
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from quantitative_trading.api.app import create_app
from quantitative_trading.api.routes import service_workflows
from quantitative_trading.config import Settings
from quantitative_trading.decision.workflow import DecisionWorkflow
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.adapters import (
    DailyBarFetchResult,
    MarketProviderError,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    DailyBar,
    DailyBarCoverageEvidence,
    DailyMoneyFlow,
    LimitStatus,
    MinuteBar,
    QuoteSnapshot,
    QuoteStatus,
    TradingStatus,
)
from quantitative_trading.recommendation.models import (
    Recommendation,
    RecommendationAction,
)
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect


SHANGHAI = ZoneInfo("Asia/Shanghai")
WEEKEND_NOW = datetime(2026, 7, 18, 10, 1, tzinfo=SHANGHAI)
SYMBOL = "600000"


class DeterministicQuoteProvider:
    def get_quotes(self, symbols):
        return {
            symbol: QuoteSnapshot(
                symbol=symbol,
                name="确定性行情样本",
                previous_close=10.4,
                open_price=10.4,
                high_price=10.6,
                low_price=10.3,
                current_price=10.5,
                change_pct=(10.5 / 10.4 - 1) * 100,
                volume=1_000_000,
                amount=10_500_000,
                trading_status=TradingStatus.NORMAL,
                limit_status=LimitStatus.NONE,
                data_time=None,
                fetched_at=WEEKEND_NOW,
                source="deterministic_e2e_quote",
                status=QuoteStatus.PARTIAL,
                warning="market source time unavailable",
            )
            for symbol in symbols
        }


class DeterministicDailyProvider:
    def __init__(self, calendar: XSHGTradingCalendar) -> None:
        self.calendar = calendar

    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        return list(
            self.get_daily_bars_with_coverage(
                symbol,
                start_date,
                end_date,
                adjustment,
            ).bars
        )

    def get_daily_bars_with_coverage(
        self,
        symbol,
        start_date,
        end_date,
        adjustment,
    ):
        if adjustment != "forward":
            raise ValueError("deterministic daily provider requires forward adjustment")
        # Keeps the first request active long enough for the browser request to
        # exercise the real 409-follow path.
        time.sleep(0.8)
        days = self.calendar.trading_days(start_date, end_date)
        bars = tuple(
            DailyBar(
                symbol=symbol,
                trade_date=trade_day,
                open=10.4,
                high=10.6,
                low=10.3,
                close=10.5,
                volume=1_000_000,
                amount=10_500_000,
                source="deterministic_e2e_daily",
                fetched_at=WEEKEND_NOW,
            )
            for trade_day in days
        )
        return DailyBarFetchResult(
            bars=bars,
            coverage_evidence=DailyBarCoverageEvidence(
                requested_start=start_date,
                requested_end=end_date,
                observed_start=start_date,
                observed_end=end_date,
                earliest_available_date=None if not bars else bars[0].trade_date,
                complete_request_window=True,
                source="deterministic_e2e_daily_full_window",
            ),
        )


class DeterministicMoneyFlowProvider:
    def __init__(self, calendar: XSHGTradingCalendar) -> None:
        self.calendar = calendar

    def get_daily_money_flow(self, symbol, start_date, end_date):
        return [
            DailyMoneyFlow(
                symbol=symbol,
                trade_date=trade_day,
                main_net_amount=1_000_000,
                main_net_pct=2,
                super_large_net_amount=600_000,
                super_large_net_pct=1.2,
                large_net_amount=400_000,
                large_net_pct=0.8,
                medium_net_amount=-300_000,
                medium_net_pct=-0.6,
                small_net_amount=-700_000,
                small_net_pct=-1.4,
                source="deterministic_e2e_flow",
                fetched_at=WEEKEND_NOW,
            )
            for trade_day in self.calendar.trading_days(start_date, end_date)
        ]


class DeterministicIntradayProvider:
    def __init__(self, calendar: XSHGTradingCalendar, *, fail: bool) -> None:
        self.calendar = calendar
        self.fail = fail

    def get_minute_bars(self, symbol, trade_date, interval):
        if self.fail:
            raise MarketProviderError("deterministic minute provider outage")
        if interval != "1m":
            raise ValueError("deterministic intraday provider requires 1m interval")
        session = self.calendar.session(trade_date)
        start = session.close_at - timedelta(minutes=29)
        return [
            MinuteBar(
                symbol=symbol,
                trade_date=trade_date,
                minute=start + timedelta(minutes=index),
                open=10.40 + index * 0.003,
                high=10.44 + index * 0.003,
                low=10.38 + index * 0.003,
                close=10.41 + index * 0.003,
                volume=100 + index,
                amount=(100 + index) * (10.41 + index * 0.003),
                source="deterministic_e2e_minute",
                fetched_at=WEEKEND_NOW,
            )
            for index in range(30)
        ]


def _recommendation(
    recommendation_id: str,
    name: str,
    *,
    created_at: datetime,
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        symbol=SYMBOL,
        name=name,
        action=RecommendationAction.HOLD,
        confidence="medium",
        position_context={"source": "manual_ledger"},
        account_context={"source": "manual_cash_account"},
        price_context={"current_price": 10.5},
        reason=["确定性端到端样本"],
        risk={"invalid_if": ["样本条件失效"], "notes": []},
        valid_until=WEEKEND_NOW + timedelta(days=1),
        data_time=created_at,
        created_at=created_at,
    )


def _seed(settings: Settings) -> None:
    old_time = datetime(2026, 7, 17, 6, 0, tzinfo=UTC)
    new_time = old_time + timedelta(minutes=3)
    with connect(settings) as connection:
        InstrumentRepository(connection).replace_catalog(
            [
                InstrumentMetadata(
                    symbol=SYMBOL,
                    name="确定性行情样本",
                    exchange=Exchange.SH,
                    instrument_type=InstrumentType.A_SHARE,
                    settlement_cycle=SettlementCycle.T1,
                    price_limit_ratio=0.10,
                    listing_date=date(1999, 11, 10),
                    metadata_source="deterministic_e2e_directory",
                    metadata_checked_at=WEEKEND_NOW,
                    rule_version="deterministic-e2e-v1",
                )
            ]
        )
        PositionRepository(connection).add(
            PositionInput(
                symbol=SYMBOL,
                name="确定性行情样本",
                quantity=1000,
                available_quantity=1000,
                cost_price=10.0,
                opened_at=date(2026, 7, 1),
            ),
            now=WEEKEND_NOW,
        )
        repository = RecommendationRepository(connection)
        repository.save_many(
            [_recommendation("rec-e2e-old", "历史样本旧", created_at=old_time)],
            created_at=old_time,
        )
        repository.save_many(
            [_recommendation("rec-e2e-new", "当前样本新", created_at=new_time)],
            created_at=new_time,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--scenario", choices=("success", "partial"), required=True)
    args = parser.parse_args()

    settings = Settings(
        database_path=args.database,
        log_dir=args.database.parent / "logs",
        enable_market_fetch=True,
        market_provider="akshare",
        api_access_password=None,
        api_token_secret="deterministic-e2e-token-secret",
    )
    calendar = XSHGTradingCalendar()

    def build_workflow(connection, _settings, *, now):
        del _settings, now
        return DecisionWorkflow(
            connection,
            calendar=calendar,
            quote_provider=DeterministicQuoteProvider(),
            daily_provider=DeterministicDailyProvider(calendar),
            money_flow_provider=DeterministicMoneyFlowProvider(calendar),
            intraday_provider=DeterministicIntradayProvider(
                calendar,
                fail=args.scenario == "partial",
            ),
            now=lambda: WEEKEND_NOW,
        )

    service_workflows._current_time = lambda: WEEKEND_NOW.astimezone(UTC)
    service_workflows.build_decision_workflow = build_workflow
    app = create_app(settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _seed(settings)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
