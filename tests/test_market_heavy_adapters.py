from datetime import UTC, date, datetime

import pandas as pd
import pytest

import quantitative_trading.market.adapters as market_adapters
from quantitative_trading.market.adapters import (
    AkShareDailyBarProvider,
    AkShareEtfDailyBarProvider,
    AkShareEtfIntradayProvider,
    AkShareIntradayProvider,
    AkShareMoneyFlowProvider,
    MarketProviderError,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import LimitStatus, QuoteStatus, TradingStatus
from quantitative_trading.market.providers import (
    AkShareEtfMarketProvider,
    AkShareMarketProvider,
    _fetch_official_etf_trading_statuses,
    _trading_status_from_sse_phase,
    _trading_status_from_szse_phase,
)


FETCHED_AT = datetime(2026, 7, 13, 7, 1, tzinfo=UTC)


class FakeAkShare:
    def __init__(self) -> None:
        self.daily_calls = []
        self.flow_calls = []
        self.minute_calls = []

    @staticmethod
    def _daily_frame():
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

    def stock_zh_a_hist(self, **kwargs):
        self.daily_calls.append(("stock_zh_a_hist", kwargs))
        return self._daily_frame()

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

    def fund_etf_hist_em(self, **kwargs):
        self.daily_calls.append(("fund_etf_hist_em", kwargs))
        return self._daily_frame()

    def fund_etf_hist_min_em(self, **kwargs):
        self.minute_calls.append(kwargs)
        return self.stock_zh_a_hist_min_em()


def test_akshare_daily_provider_requests_forward_adjustment_and_converts_lots() -> None:
    akshare = FakeAkShare()
    provider = AkShareDailyBarProvider(akshare_module=akshare, now=lambda: FETCHED_AT)

    bars = provider.get_daily_bars(
        "600000", date(2026, 7, 10), date(2026, 7, 10), "forward"
    )

    assert akshare.daily_calls[0][1]["adjust"] == "qfq"
    assert bars[0].volume == 12_300
    assert bars[0].amount == 129_150


@pytest.mark.parametrize(
    ("provider_type", "symbol", "endpoint", "evidence_source"),
    [
        (
            AkShareDailyBarProvider,
            "600000",
            "stock_zh_a_hist",
            "akshare_daily_full_window",
        ),
        (
            AkShareEtfDailyBarProvider,
            "510300",
            "fund_etf_hist_em",
            "akshare_etf_daily_full_window",
        ),
    ],
)
def test_akshare_daily_provider_returns_complete_window_coverage_evidence(
    provider_type,
    symbol: str,
    endpoint: str,
    evidence_source: str,
) -> None:
    akshare = FakeAkShare()
    provider = provider_type(akshare_module=akshare, now=lambda: FETCHED_AT)
    requested_start = date(2026, 7, 1)
    requested_end = date(2026, 7, 10)

    result = provider.get_daily_bars_with_coverage(
        symbol,
        requested_start,
        requested_end,
        "forward",
    )

    assert isinstance(provider, market_adapters.DailyBarCoverageProvider)
    assert akshare.daily_calls == [
        (
            endpoint,
            {
                "symbol": symbol,
                "period": "daily",
                "start_date": "20260701",
                "end_date": "20260710",
                "adjust": "qfq",
            },
        )
    ]
    assert [bar.trade_date for bar in result.bars] == [requested_end]
    assert result.coverage_evidence.model_dump() == {
        "requested_start": requested_start,
        "requested_end": requested_end,
        "observed_start": requested_start,
        "observed_end": requested_end,
        "earliest_available_date": requested_end,
        "complete_request_window": True,
        "source": evidence_source,
    }


def test_legacy_daily_provider_is_not_misclassified_as_coverage_capable() -> None:
    class LegacyDailyProvider:
        @staticmethod
        def get_daily_bars(symbol, start_date, end_date, adjustment):
            return []

    assert not isinstance(
        LegacyDailyProvider(),
        market_adapters.DailyBarCoverageProvider,
    )


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


@pytest.mark.parametrize(
    ("provider_type", "symbol", "prefixed_symbol"),
    [
        (AkShareIntradayProvider, "002555", "sz002555"),
        (AkShareEtfIntradayProvider, "515880", "sh515880"),
    ],
)
def test_akshare_minute_provider_falls_back_to_sina_with_share_volume(
    provider_type,
    symbol: str,
    prefixed_symbol: str,
) -> None:
    class FallbackAkShare:
        def __init__(self) -> None:
            self.fallback_calls = []

        @staticmethod
        def stock_zh_a_hist_min_em(**kwargs):
            raise ConnectionError("eastmoney minute unavailable")

        @staticmethod
        def fund_etf_hist_min_em(**kwargs):
            raise ConnectionError("eastmoney minute unavailable")

        def stock_zh_a_minute(self, **kwargs):
            self.fallback_calls.append(kwargs)
            return pd.DataFrame(
                [
                    {
                        "day": "2026-07-13 10:00:00",
                        "open": "10.00",
                        "high": "10.20",
                        "low": "9.90",
                        "close": "10.10",
                        "volume": "96300",
                        "amount": "972630",
                    },
                    {
                        "day": "2026-07-13 12:00:00",
                        "open": "10.10",
                        "high": "10.10",
                        "low": "10.10",
                        "close": "10.10",
                        "volume": "100",
                        "amount": "1010",
                    },
                ]
            )

    akshare = FallbackAkShare()
    provider = provider_type(
        calendar=XSHGTradingCalendar(),
        akshare_module=akshare,
        now=lambda: FETCHED_AT,
    )

    bars = provider.get_minute_bars(symbol, date(2026, 7, 13), "1m")

    assert akshare.fallback_calls == [
        {"symbol": prefixed_symbol, "period": "1", "adjust": ""}
    ]
    assert len(bars) == 1
    assert bars[0].volume == 96_300
    assert bars[0].amount == 972_630
    assert bars[0].source == "akshare_sina_minute"


def test_akshare_minute_provider_reports_failure_when_both_sources_fail() -> None:
    class FailingAkShare:
        @staticmethod
        def stock_zh_a_hist_min_em(**kwargs):
            raise ConnectionError("eastmoney minute unavailable")

        @staticmethod
        def stock_zh_a_minute(**kwargs):
            raise ConnectionError("sina minute unavailable")

    provider = AkShareIntradayProvider(
        calendar=XSHGTradingCalendar(),
        akshare_module=FailingAkShare(),
        now=lambda: FETCHED_AT,
    )

    with pytest.raises(MarketProviderError, match="intraday market provider request failed"):
        provider.get_minute_bars("002555", date(2026, 7, 13), "1m")


def test_akshare_etf_daily_and_minute_providers_use_fund_endpoints() -> None:
    akshare = FakeAkShare()
    daily = AkShareEtfDailyBarProvider(akshare_module=akshare, now=lambda: FETCHED_AT)
    intraday = AkShareEtfIntradayProvider(
        calendar=XSHGTradingCalendar(), akshare_module=akshare, now=lambda: FETCHED_AT
    )

    bars = daily.get_daily_bars(
        "510300", date(2026, 7, 10), date(2026, 7, 10), "forward"
    )
    minutes = intraday.get_minute_bars("510300", date(2026, 7, 13), "1m")

    assert bars[0].symbol == "510300"
    assert bars[0].volume == 12_300
    assert minutes[0].symbol == "510300"
    assert any(call.get("symbol") == "510300" for _, call in akshare.daily_calls)
    assert any(call.get("symbol") == "510300" for call in akshare.minute_calls)


def test_akshare_etf_quote_provider_uses_fund_spot_endpoint() -> None:
    class EtfQuoteAkShare:
        calls = 0

        @classmethod
        def fund_etf_spot_em(cls):
            cls.calls += 1
            return pd.DataFrame(
                [
                    {
                        "代码": "510300",
                        "名称": "沪深300ETF",
                        "昨收": 4.0,
                        "开盘价": 4.01,
                        "最高价": 4.08,
                        "最低价": 3.99,
                        "最新价": 4.05,
                        "涨跌幅": 1.25,
                        "成交量": 123,
                        "成交额": 49_815,
                        "更新时间": pd.Timestamp("2026-07-13 15:00:00+08:00"),
                    }
                ]
            )

    provider = AkShareEtfMarketProvider(
        akshare_module=EtfQuoteAkShare,
        now=lambda: FETCHED_AT,
        price_limit_ratios={"510300": 0.1},
        trading_phase_fetcher=lambda symbols: {"510300": TradingStatus.NORMAL},
    )
    quote = provider.get_quotes(["510300"])["510300"]

    assert EtfQuoteAkShare.calls == 1
    assert quote.name == "沪深300ETF"
    assert quote.open_price == 4.01
    assert quote.high_price == 4.08
    assert quote.low_price == 3.99
    assert quote.volume == 12_300
    assert quote.data_time.isoformat() == "2026-07-13T15:00:00+08:00"
    assert quote.trading_status is TradingStatus.NORMAL
    assert quote.limit_status is LimitStatus.NONE
    assert quote.status is QuoteStatus.OK
    assert quote.source == "akshare_etf"


def test_akshare_etf_quote_derives_limit_status_from_verified_ratio() -> None:
    class EtfQuoteAkShare:
        @staticmethod
        def fund_etf_spot_em():
            return pd.DataFrame(
                [
                    {"代码": "510300", "名称": "上限", "昨收": 4.001, "最新价": 4.401},
                    {"代码": "159919", "名称": "下限", "昨收": 4.001, "最新价": 3.601},
                    {"代码": "513100", "名称": "区间内", "昨收": 4.001, "最新价": 4.2},
                ]
            )

    provider = AkShareEtfMarketProvider(
        akshare_module=EtfQuoteAkShare,
        now=lambda: FETCHED_AT,
        price_limit_ratios={"510300": 0.1, "159919": 0.1, "513100": 0.1},
        trading_phase_fetcher=lambda symbols: {},
    )
    quotes = provider.get_quotes(["510300", "159919", "513100"])

    assert quotes["510300"].limit_status is LimitStatus.UP
    assert quotes["159919"].limit_status is LimitStatus.DOWN
    assert quotes["513100"].limit_status is LimitStatus.NONE
    assert all(
        quote.trading_status is TradingStatus.UNKNOWN for quote in quotes.values()
    )


def test_akshare_etf_quote_keeps_limit_unknown_without_reliable_inputs() -> None:
    class EtfQuoteAkShare:
        @staticmethod
        def fund_etf_spot_em():
            return pd.DataFrame(
                [
                    {"代码": "510300", "名称": "无比例", "昨收": 4.0, "最新价": 4.4},
                    {"代码": "159919", "名称": "无昨收", "最新价": 4.4},
                    {"代码": "513100", "名称": "越界", "昨收": 4.0, "最新价": 4.5},
                    {"代码": "588000", "名称": "昨收单位异常", "昨收": 4.0005, "最新价": 4.2},
                ]
            )

    provider = AkShareEtfMarketProvider(
        akshare_module=EtfQuoteAkShare,
        now=lambda: FETCHED_AT,
        price_limit_ratios={"159919": 0.1, "513100": 0.1, "588000": 0.1},
        trading_phase_fetcher=lambda symbols: {},
    )
    quotes = provider.get_quotes(["510300", "159919", "513100", "588000"])

    assert all(quote.limit_status is LimitStatus.UNKNOWN for quote in quotes.values())
    assert "price_limit_ratio unavailable" in quotes["510300"].warning
    assert "previous close unavailable" in quotes["159919"].warning
    assert "outside calculated price limits" in quotes["513100"].warning
    assert "invalid ETF price tick" in quotes["588000"].warning


def test_akshare_etf_quote_maps_explicit_trading_phase_statuses() -> None:
    class EtfQuoteAkShare:
        @staticmethod
        def fund_etf_spot_em():
            return pd.DataFrame(
                [
                    {"代码": "510300", "名称": "正常", "最新价": 4.2},
                    {"代码": "159919", "名称": "停牌", "最新价": 5.0},
                    {"代码": "588000", "名称": "未知", "最新价": 1.0},
                ]
            )

    provider = AkShareEtfMarketProvider(
        akshare_module=EtfQuoteAkShare,
        now=lambda: FETCHED_AT,
        trading_phase_fetcher=lambda symbols: {
            "510300": TradingStatus.NORMAL,
            "159919": TradingStatus.SUSPENDED,
            "588000": TradingStatus.UNKNOWN,
        },
    )
    quotes = provider.get_quotes(["510300", "159919", "588000"])

    assert quotes["510300"].trading_status is TradingStatus.NORMAL
    assert quotes["159919"].trading_status is TradingStatus.SUSPENDED
    assert quotes["588000"].trading_status is TradingStatus.UNKNOWN


def test_akshare_etf_quote_phase_failure_only_degrades_status() -> None:
    class EtfQuoteAkShare:
        @staticmethod
        def fund_etf_spot_em():
            return pd.DataFrame(
                [{"代码": "510300", "名称": "沪深300ETF", "最新价": 4.2}]
            )

    def fail_phase_fetch(symbols):
        raise RuntimeError("official phase source unavailable")

    provider = AkShareEtfMarketProvider(
        akshare_module=EtfQuoteAkShare,
        now=lambda: FETCHED_AT,
        trading_phase_fetcher=fail_phase_fetch,
    )
    quote = provider.get_quotes(["510300"])["510300"]

    assert quote.current_price == 4.2
    assert quote.status is QuoteStatus.PARTIAL
    assert quote.trading_status is TradingStatus.UNKNOWN
    assert "official trading phase fetch failed" in quote.warning


@pytest.mark.parametrize("code", ["00", "02", "03", "05", "07", "11"])
def test_szse_official_normal_phase_codes(code: str) -> None:
    assert _trading_status_from_szse_phase(code) is TradingStatus.NORMAL


@pytest.mark.parametrize("code", ["04", "06", "12"])
def test_szse_official_suspended_phase_codes(code: str) -> None:
    assert _trading_status_from_szse_phase(code) is TradingStatus.SUSPENDED


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("T110", TradingStatus.NORMAL),
        ("E110", TradingStatus.NORMAL),
        ("P010", TradingStatus.SUSPENDED),
        ("X000", TradingStatus.UNKNOWN),
    ],
)
def test_sse_official_tradephase_codes(
    phase: str, expected: TradingStatus
) -> None:
    assert _trading_status_from_sse_phase(phase) is expected


def test_default_official_phase_fetcher_uses_sse_batch_then_szse(monkeypatch) -> None:
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, *, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        if "yunhq.sse.com.cn" in url:
            return Response({"list": [["510300", "T110"]]})
        return Response(
            {
                "data": {
                    "code": params["code"],
                    "tradingPhaseCode1": "04",
                }
            }
        )

    monkeypatch.setattr("quantitative_trading.market.providers.requests.get", fake_get)

    statuses = _fetch_official_etf_trading_statuses(["510300", "159919"])

    assert statuses == {
        "510300": TradingStatus.NORMAL,
        "159919": TradingStatus.SUSPENDED,
    }
    assert calls[0][1]["select"] == "code,tradephase"
    assert calls[1][1] == {"marketId": 1, "code": "159919"}


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
