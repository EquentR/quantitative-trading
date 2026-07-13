from datetime import UTC, date, datetime

import pandas as pd

from quantitative_trading.market.adapters import (
    AkShareDailyBarProvider,
    AkShareIntradayProvider,
    AkShareMoneyFlowProvider,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import LimitStatus, QuoteStatus, TradingStatus
from quantitative_trading.market.providers import AkShareMarketProvider


FETCHED_AT = datetime(2026, 7, 13, 7, 1, tzinfo=UTC)


class FakeAkShare:
    def __init__(self) -> None:
        self.daily_calls = []
        self.flow_calls = []
        self.minute_calls = []

    def stock_zh_a_hist(self, **kwargs):
        self.daily_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "日期": "2026-07-10",
                    "开盘": 10,
                    "收盘": 10.5,
                    "最高": 11,
                    "最低": 9.8,
                    "成交量": 123,
                    "成交额": 129_150,
                }
            ]
        )

    def stock_individual_fund_flow(self, **kwargs):
        self.flow_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "日期": "2026-07-10",
                    "主力净流入-净额": 100,
                    "主力净流入-净占比": 1.1,
                    "超大单净流入-净额": 40,
                    "超大单净流入-净占比": 0.4,
                    "大单净流入-净额": 60,
                    "大单净流入-净占比": 0.6,
                    "中单净流入-净额": -20,
                    "中单净流入-净占比": -0.2,
                    "小单净流入-净额": -80,
                    "小单净流入-净占比": -0.8,
                }
            ]
        )

    def stock_zh_a_hist_min_em(self, **kwargs):
        self.minute_calls.append(kwargs)
        return pd.DataFrame(
            [
                {
                    "时间": "2026-07-13 10:00:00",
                    "开盘": 10,
                    "收盘": 10.1,
                    "最高": 10.2,
                    "最低": 9.9,
                    "成交量": 5,
                    "成交额": 5_050,
                },
                {
                    "时间": "2026-07-13 12:00:00",
                    "开盘": 10,
                    "收盘": 10,
                    "最高": 10,
                    "最低": 10,
                    "成交量": 1,
                    "成交额": 1_000,
                },
            ]
        )


def test_akshare_daily_provider_requests_forward_adjustment_and_converts_lots() -> None:
    akshare = FakeAkShare()
    provider = AkShareDailyBarProvider(akshare_module=akshare, now=lambda: FETCHED_AT)

    bars = provider.get_daily_bars(
        "600000", date(2026, 7, 10), date(2026, 7, 10), "forward"
    )

    assert akshare.daily_calls[0]["adjust"] == "qfq"
    assert bars[0].volume == 12_300
    assert bars[0].amount == 129_150


def test_akshare_money_flow_maps_complete_split_in_yuan_and_percentage_points() -> None:
    akshare = FakeAkShare()
    provider = AkShareMoneyFlowProvider(akshare_module=akshare, now=lambda: FETCHED_AT)

    flows = provider.get_daily_money_flow(
        "600000", date(2026, 7, 10), date(2026, 7, 10)
    )

    assert akshare.flow_calls == [{"stock": "600000", "market": "sh"}]
    assert flows[0].main_net_amount == 100
    assert flows[0].super_large_net_pct == 0.4
    assert flows[0].small_net_amount == -80


def test_akshare_minute_provider_converts_lots_and_discards_lunch_rows() -> None:
    akshare = FakeAkShare()
    provider = AkShareIntradayProvider(
        calendar=XSHGTradingCalendar(),
        akshare_module=akshare,
        now=lambda: FETCHED_AT,
    )

    bars = provider.get_minute_bars("600000", date(2026, 7, 13), "1m")

    assert akshare.minute_calls[0]["period"] == "1"
    assert len(bars) == 1
    assert bars[0].minute.isoformat() == "2026-07-13T10:00:00+08:00"
    assert bars[0].volume == 500


def test_akshare_quote_maps_reliable_standard_fields_and_statuses() -> None:
    class RichQuoteAkShare:
        @staticmethod
        def stock_zh_a_spot_em():
            return pd.DataFrame(
                [
                    {
                        "代码": "600000",
                        "名称": "浦发银行",
                        "昨收": 10.0,
                        "今开": 10.1,
                        "最高": 10.8,
                        "最低": 9.9,
                        "最新价": 10.5,
                        "涨跌幅": 5.0,
                        "成交量": 123,
                        "成交额": 129_150,
                    }
                ]
            )

    quote = AkShareMarketProvider(
        akshare_module=RichQuoteAkShare, now=lambda: FETCHED_AT
    ).get_quotes(["600000"])["600000"]

    assert quote.previous_close == 10.0
    assert quote.open_price == 10.1
    assert quote.high_price == 10.8
    assert quote.low_price == 9.9
    assert quote.volume == 12_300
    assert quote.amount == 129_150
    assert quote.trading_status is TradingStatus.UNKNOWN
    assert quote.limit_status is LimitStatus.UNKNOWN
    assert quote.status is QuoteStatus.PARTIAL
    assert "trading_status" in quote.warning
    assert "limit_status" in quote.warning


def test_akshare_quote_missing_standard_fields_is_partial_and_does_not_guess() -> None:
    class SparseQuoteAkShare:
        @staticmethod
        def stock_zh_a_spot_em():
            return pd.DataFrame(
                [
                    {
                        "代码": "600000",
                        "名称": "浦发银行",
                        "最新价": 10.5,
                        "涨跌幅": 5.0,
                    }
                ]
            )

    quote = AkShareMarketProvider(
        akshare_module=SparseQuoteAkShare, now=lambda: FETCHED_AT
    ).get_quotes(["600000"])["600000"]

    assert quote.status is QuoteStatus.PARTIAL
    assert quote.previous_close is None
    assert quote.open_price is None
    assert quote.high_price is None
    assert quote.low_price is None
    assert quote.volume is None
    assert quote.amount is None
    assert quote.trading_status is TradingStatus.UNKNOWN
    assert quote.limit_status is LimitStatus.UNKNOWN
