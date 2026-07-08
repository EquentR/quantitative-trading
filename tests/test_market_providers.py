from collections.abc import Sequence
from datetime import UTC, datetime

import pandas as pd
import pytest

from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
from quantitative_trading.market.providers import (
    AkShareMarketProvider,
    DisabledMarketProvider,
    MarketDataProvider,
)


class SparseFakeMarketProvider:
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        return {
            "600000": QuoteSnapshot(
                symbol="600000",
                name="Pufa Bank",
                current_price=10.5,
                change_pct=1.2,
                data_time=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                fetched_at=datetime(2026, 7, 7, 2, 30, 3, tzinfo=UTC),
                source="fake",
                status=QuoteStatus.OK,
            )
        }


def test_market_data_provider_contract_allows_sparse_quote_mapping() -> None:
    provider: MarketDataProvider = SparseFakeMarketProvider()

    quotes = provider.get_quotes(["600000", "000001"])

    assert set(quotes) == {"600000"}
    assert "000001" not in quotes


def test_disabled_market_provider_returns_failed_quotes_for_each_symbol() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider: MarketDataProvider = DisabledMarketProvider(now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "000001"])

    assert set(quotes) == {"600000", "000001"}
    assert quotes["600000"].symbol == "600000"
    assert quotes["600000"].status is QuoteStatus.FAILED
    assert quotes["600000"].source == "disabled"
    assert quotes["600000"].warning == "market fetch disabled"
    assert quotes["600000"].fetched_at == fetched_at
    assert quotes["000001"].symbol == "000001"
    assert quotes["000001"].status is QuoteStatus.FAILED
    assert quotes["000001"].source == "disabled"
    assert quotes["000001"].warning == "market fetch disabled"
    assert quotes["000001"].fetched_at == fetched_at


def test_disabled_market_provider_returns_empty_mapping_for_empty_symbol_list() -> None:
    provider = DisabledMarketProvider(now=lambda: datetime(2026, 7, 7, 2, 30, tzinfo=UTC))

    assert provider.get_quotes([]) == {}


class FakeAkShare:
    calls = 0

    @classmethod
    def stock_zh_a_spot_em(cls):
        cls.calls += 1
        return pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行", "最新价": 10.5, "涨跌幅": 1.2},
                {"代码": "000001", "名称": "平安银行", "最新价": 12.3, "涨跌幅": -0.5},
            ]
        )


def test_akshare_provider_maps_dataframe_to_ok_quote() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=FakeAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000"])

    assert quotes["600000"].symbol == "600000"
    assert quotes["600000"].name == "浦发银行"
    assert quotes["600000"].current_price == 10.5
    assert quotes["600000"].change_pct == 1.2
    assert quotes["600000"].data_time == fetched_at
    assert quotes["600000"].fetched_at == fetched_at
    assert quotes["600000"].source == "akshare"
    assert quotes["600000"].status is QuoteStatus.OK


def test_akshare_provider_returns_failed_quote_when_symbol_not_found() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=FakeAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "300750"])

    assert quotes["600000"].status is QuoteStatus.OK
    assert quotes["300750"].symbol == "300750"
    assert quotes["300750"].status is QuoteStatus.FAILED
    assert quotes["300750"].source == "akshare"
    assert quotes["300750"].warning == "quote not found"
    assert quotes["300750"].fetched_at == fetched_at


class FailingAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        raise RuntimeError("service unavailable")


class SensitiveFailingAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        raise RuntimeError(
            "service unavailable token=supersecret Authorization: Bearer abc "
            "at /tmp/private.db"
        )


def failing_quote_fetcher(url: str, *, timeout: float):
    raise RuntimeError("fallback unavailable")


def test_akshare_provider_wraps_fetch_failure_for_all_requested_symbols() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(
        akshare_module=FailingAkShare,
        now=lambda: fetched_at,
        eastmoney_single_quote_fetcher=failing_quote_fetcher,
        tencent_quote_fetcher=failing_quote_fetcher,
    )

    quotes = provider.get_quotes(["600000", "000001"])

    assert set(quotes) == {"600000", "000001"}
    for quote in quotes.values():
        assert quote.status is QuoteStatus.FAILED
        assert quote.source == "akshare"
        assert quote.fetched_at == fetched_at
        assert "akshare quote fetch failed" in quote.warning
        assert "service unavailable" in quote.warning


def test_akshare_provider_falls_back_to_single_quote_fetch_when_batch_fetch_fails() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    requested_urls: list[str] = []

    def single_quote_fetcher(url: str, *, timeout: float):
        requested_urls.append(url)
        assert timeout > 0
        assert url.startswith("https://82.push2.eastmoney.com/")
        if "secid=0.002555" in url:
            return {
                "data": {
                    "f57": "002555",
                    "f58": "三七互娱",
                    "f43": 1883,
                    "f170": -309,
                }
            }
        if "secid=1.603459" in url:
            return {
                "data": {
                    "f57": "603459",
                    "f58": "红板科技",
                    "f43": 8972,
                    "f170": -2,
                }
            }
        if "secid=1.515880" in url:
            return {
                "data": {
                    "f57": "515880",
                    "f58": "通信ETF国泰",
                    "f43": 780,
                    "f170": 250,
                }
            }
        raise AssertionError(f"unexpected url: {url}")

    provider = AkShareMarketProvider(
        akshare_module=FailingAkShare,
        now=lambda: fetched_at,
        eastmoney_single_quote_fetcher=single_quote_fetcher,
    )

    quotes = provider.get_quotes(["002555", "515880", "603459"])

    assert quotes["002555"].status is QuoteStatus.OK
    assert quotes["002555"].name == "三七互娱"
    assert quotes["002555"].current_price == 18.83
    assert quotes["002555"].change_pct == -3.09
    assert quotes["515880"].status is QuoteStatus.OK
    assert quotes["515880"].name == "通信ETF国泰"
    assert quotes["515880"].current_price == 0.78
    assert quotes["515880"].change_pct == 2.5
    assert quotes["603459"].status is QuoteStatus.OK
    assert quotes["603459"].name == "红板科技"
    assert quotes["603459"].current_price == 89.72
    assert quotes["603459"].change_pct == -0.02
    assert all(quote.source == "eastmoney_single_quote" for quote in quotes.values())
    assert len(requested_urls) == 3


def test_akshare_provider_falls_back_to_tencent_quotes_when_eastmoney_single_quote_fails() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)

    def failing_single_quote_fetcher(url: str, *, timeout: float):
        raise RuntimeError("eastmoney disconnected")

    def tencent_line(prefix: str, symbol: str, name: str, price: str, change_pct: str) -> str:
        fields = [""] * 88
        fields[1] = name
        fields[2] = symbol
        fields[3] = price
        fields[32] = change_pct
        return f'v_{prefix}{symbol}="' + "~".join(fields) + '";'

    def tencent_quote_fetcher(url: str, *, timeout: float):
        assert timeout > 0
        assert "q=sz002555,sh515880,sh603459" in url
        return "\n".join(
            [
                tencent_line("sz", "002555", "三七互娱", "18.83", "-3.09"),
                tencent_line("sh", "515880", "通信ETF国泰", "0.780", "2.50"),
                tencent_line("sh", "603459", "红板科技", "89.72", "-0.02"),
            ]
        )

    provider = AkShareMarketProvider(
        akshare_module=FailingAkShare,
        now=lambda: fetched_at,
        eastmoney_single_quote_fetcher=failing_single_quote_fetcher,
        tencent_quote_fetcher=tencent_quote_fetcher,
    )

    quotes = provider.get_quotes(["002555", "515880", "603459"])

    assert quotes["002555"].status is QuoteStatus.OK
    assert quotes["002555"].name == "三七互娱"
    assert quotes["002555"].current_price == 18.83
    assert quotes["002555"].change_pct == -3.09
    assert quotes["515880"].status is QuoteStatus.OK
    assert quotes["515880"].name == "通信ETF国泰"
    assert quotes["515880"].current_price == 0.78
    assert quotes["515880"].change_pct == 2.5
    assert quotes["603459"].status is QuoteStatus.OK
    assert quotes["603459"].name == "红板科技"
    assert quotes["603459"].current_price == 89.72
    assert quotes["603459"].change_pct == -0.02
    assert all(quote.source == "tencent_quote" for quote in quotes.values())


def test_akshare_provider_sanitizes_fetch_failure_warning() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(
        akshare_module=SensitiveFailingAkShare,
        now=lambda: fetched_at,
        eastmoney_single_quote_fetcher=failing_quote_fetcher,
        tencent_quote_fetcher=failing_quote_fetcher,
    )

    quote = provider.get_quotes(["600000"])["600000"]

    assert quote.status is QuoteStatus.FAILED
    assert "akshare quote fetch failed" in quote.warning
    assert "service unavailable" in quote.warning
    assert "supersecret" not in quote.warning
    assert "Bearer abc" not in quote.warning
    assert "/tmp/private.db" not in quote.warning


class EmptyInputAkShare:
    calls = 0

    @classmethod
    def stock_zh_a_spot_em(cls):
        cls.calls += 1
        return pd.DataFrame()


def test_akshare_provider_returns_empty_mapping_without_fetch_for_empty_symbol_list() -> None:
    provider = AkShareMarketProvider(
        akshare_module=EmptyInputAkShare,
        now=lambda: datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
    )

    assert provider.get_quotes([]) == {}
    assert EmptyInputAkShare.calls == 0


class MissingNameAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        return pd.DataFrame(
            [
                {"代码": "600000", "最新价": 10.5, "涨跌幅": 1.2},
                {"代码": "000001", "名称": "  ", "最新价": 12.3, "涨跌幅": -0.5},
            ]
        )


def test_akshare_provider_returns_partial_quote_when_name_is_missing_or_blank() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=MissingNameAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "000001"])

    for symbol in ("600000", "000001"):
        assert quotes[symbol].status is QuoteStatus.PARTIAL
        assert quotes[symbol].name == ""
        assert quotes[symbol].current_price in {10.5, 12.3}
        assert quotes[symbol].change_pct in {1.2, -0.5}
        assert quotes[symbol].data_time == fetched_at
        assert quotes[symbol].fetched_at == fetched_at
        assert quotes[symbol].source == "akshare"
        assert "名称" in quotes[symbol].warning


class MissingChangePctAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        return pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行", "最新价": 10.5},
                {"代码": "000001", "名称": "平安银行", "最新价": 12.3, "涨跌幅": "not a number"},
            ]
        )


def test_akshare_provider_returns_partial_quote_when_change_pct_is_missing_or_bad() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=MissingChangePctAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "000001"])

    for symbol in ("600000", "000001"):
        assert quotes[symbol].status is QuoteStatus.PARTIAL
        assert quotes[symbol].name in {"浦发银行", "平安银行"}
        assert quotes[symbol].current_price in {10.5, 12.3}
        assert quotes[symbol].change_pct is None
        assert quotes[symbol].data_time == fetched_at
        assert quotes[symbol].fetched_at == fetched_at
        assert quotes[symbol].source == "akshare"
        assert "涨跌幅" in quotes[symbol].warning


class BadOptionalFieldsAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        return pd.DataFrame(
            [{"代码": "600000", "名称": None, "最新价": 10.5, "涨跌幅": float("inf")}]
        )


def test_akshare_provider_combines_partial_warnings_when_name_and_change_pct_are_bad() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=BadOptionalFieldsAkShare, now=lambda: fetched_at)

    quote = provider.get_quotes(["600000"])["600000"]

    assert quote.status is QuoteStatus.PARTIAL
    assert quote.name == ""
    assert quote.current_price == 10.5
    assert quote.change_pct is None
    assert quote.data_time == fetched_at
    assert "名称" in quote.warning
    assert "涨跌幅" in quote.warning


class MixedQualityAkShare:
    @staticmethod
    def stock_zh_a_spot_em():
        return pd.DataFrame(
            [
                {"代码": "600000", "名称": "浦发银行", "最新价": float("nan"), "涨跌幅": 1.2},
                {"代码": "000001", "名称": "平安银行", "最新价": 12.3, "涨跌幅": -0.5},
            ]
        )


def test_akshare_provider_bad_price_does_not_block_other_quotes() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=MixedQualityAkShare, now=lambda: fetched_at)

    quotes = provider.get_quotes(["600000", "000001"])

    assert quotes["600000"].status is QuoteStatus.FAILED
    assert "akshare quote mapping failed" in quotes["600000"].warning
    assert "最新价" in quotes["600000"].warning
    assert quotes["000001"].status is QuoteStatus.OK
    assert quotes["000001"].current_price == 12.3


def test_akshare_provider_serializes_only_internal_quote_fields() -> None:
    fetched_at = datetime(2026, 7, 7, 2, 30, tzinfo=UTC)
    provider = AkShareMarketProvider(akshare_module=FakeAkShare, now=lambda: fetched_at)

    payload_json = provider.get_quotes(["600000"])["600000"].model_dump_json()

    assert "代码" not in payload_json
    assert "最新价" not in payload_json
    assert "涨跌幅" not in payload_json
    assert '"current_price"' in payload_json
    assert '"change_pct"' in payload_json


class CountingAkShare(FakeAkShare):
    calls = 0


def test_akshare_provider_rejects_naive_fetch_time_before_fetch() -> None:
    provider = AkShareMarketProvider(
        akshare_module=CountingAkShare,
        now=lambda: datetime(2026, 7, 7, 2, 30),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        provider.get_quotes(["600000"])

    assert CountingAkShare.calls == 0
