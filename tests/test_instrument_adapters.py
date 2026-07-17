from datetime import UTC, date, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from quantitative_trading.instrument.adapters import (
    AkShareInstrumentDirectoryAdapter,
    InstrumentDirectoryProviderError,
    SseFundClassificationSource,
)
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentType,
    SettlementCycle,
)
from tests.instrument_fixtures import etf_name_variant_metadata


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)
TRADE_DATE = date(2026, 7, 14)


class FakeSseFundClassificationSource:
    def __init__(
        self,
        subclasses: dict[str, str] | None = None,
        listing_dates: dict[str, date] | None = None,
        listing_date_conflicts: frozenset[str] = frozenset(),
    ) -> None:
        self.subclasses = {"510300": "03"} if subclasses is None else subclasses
        self.listing_dates = (
            {"510300": date(2012, 5, 28)}
            if listing_dates is None
            else listing_dates
        )
        self.listing_date_conflicts = listing_date_conflicts
        self.calls = 0

    def fetch(self):  # noqa: ANN201
        self.calls += 1
        return self.subclasses

    def fetch_metadata(self):  # noqa: ANN201
        self.calls += 1
        return SimpleNamespace(
            subclasses=self.subclasses,
            listing_dates=self.listing_dates,
            listing_date_conflicts=self.listing_date_conflicts,
            warnings=(),
        )


def directory_adapter(akshare, *, source=None):  # noqa: ANN001, ANN201
    return AkShareInstrumentDirectoryAdapter(
        akshare_module=akshare,
        sse_fund_classification_source=source or FakeSseFundClassificationSource(),
        now=lambda: NOW,
    )


class FakeAkShareDirectory:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def stock_info_sh_name_code(self, *, symbol: str) -> pd.DataFrame:
        self.calls.append(("sh_a", symbol))
        if symbol == "主板A股":
            return pd.DataFrame(
                [
                    {
                        "证券代码": "600519",
                        "证券简称": "贵州茅台",
                        "上市日期": date(2001, 8, 27),
                    }
                ]
            )
        return pd.DataFrame(
            [
                {
                    "证券代码": "688001",
                    "证券简称": "华兴源创",
                    "上市日期": date(2019, 7, 22),
                }
            ]
        )

    def stock_info_sz_name_code(self, *, symbol: str) -> pd.DataFrame:
        self.calls.append(("sz_a", symbol))
        return pd.DataFrame(
            [
                {
                    "A股代码": "000001",
                    "A股简称": "平安银行",
                    "A股上市日期": date(1991, 4, 3),
                }
            ]
        )

    def fund_etf_spot_em(self) -> pd.DataFrame:
        self.calls.append(("etf_spot", None))
        return pd.DataFrame(
            [
                {"代码": "510300", "名称": "沪深300ETF"},
                {"代码": "159915", "名称": "创业板ETF"},
                {"代码": "160000", "名称": "不支持的基金"},
            ]
        )

    def fund_etf_scale_sse(self, *, date: str) -> pd.DataFrame:
        self.calls.append(("sse_etf", date))
        return pd.DataFrame(
            [
                {
                    "基金代码": "510300",
                    "基金简称": "沪深300ETF",
                    "ETF类型": "跨市",
                    "统计日期": TRADE_DATE,
                }
            ]
        )

    def fund_etf_scale_szse(self) -> pd.DataFrame:
        self.calls.append(("szse_etf", None))
        return pd.DataFrame(
            [
                {
                    "基金代码": "159915",
                    "基金简称": "创业板ETF",
                    "基金类别": "ETF",
                    "投资类别": "股票基金",
                    "上市日期": date(2011, 12, 9),
                },
                {
                    "基金代码": "160000",
                    "基金简称": "不支持的基金",
                    "基金类别": "LOF",
                    "投资类别": "股票基金",
                    "上市日期": date(2020, 1, 1),
                },
            ]
        )


def test_directory_maps_exchange_specific_a_shares_and_verified_etfs() -> None:
    akshare = FakeAkShareDirectory()
    classifications = FakeSseFundClassificationSource()
    adapter = directory_adapter(akshare, source=classifications)

    snapshot = adapter.fetch(TRADE_DATE)
    by_symbol = {item.symbol: item for item in snapshot.items}

    assert list(by_symbol) == ["000001", "159915", "510300", "600519", "688001"]
    assert by_symbol["600519"].exchange is Exchange.SH
    assert by_symbol["000001"].exchange is Exchange.SZ
    assert by_symbol["600519"].instrument_type is InstrumentType.A_SHARE
    assert by_symbol["600519"].settlement_cycle is SettlementCycle.T1
    assert by_symbol["510300"].instrument_type is InstrumentType.ETF
    assert by_symbol["510300"].settlement_cycle is SettlementCycle.T1
    assert by_symbol["159915"].exchange is Exchange.SZ
    assert by_symbol["600519"].listing_date == date(2001, 8, 27)
    assert by_symbol["000001"].listing_date == date(1991, 4, 3)
    assert by_symbol["510300"].listing_date == date(2012, 5, 28)
    assert by_symbol["159915"].listing_date == date(2011, 12, 9)
    assert "160000" not in by_symbol
    assert akshare.calls == [
        ("sh_a", "主板A股"),
        ("sh_a", "科创板"),
        ("sz_a", "A股列表"),
        ("etf_spot", None),
        ("sse_etf", "20260714"),
        ("szse_etf", None),
    ]
    assert snapshot.source_trade_dates[adapter.SH_ETF_SOURCE] == TRADE_DATE
    assert classifications.calls == 1


def test_directory_listing_date_conflict_degrades_instrument_to_unknown() -> None:
    class ListingConflictDirectory(FakeAkShareDirectory):
        def stock_info_sh_name_code(self, *, symbol: str) -> pd.DataFrame:
            if symbol != "主板A股":
                return super().stock_info_sh_name_code(symbol=symbol)
            self.calls.append(("sh_a", symbol))
            return pd.DataFrame(
                [
                    {
                        "证券代码": "600519",
                        "证券简称": "贵州茅台",
                        "上市日期": date(2001, 8, 27),
                    },
                    {
                        "证券代码": "600519",
                        "证券简称": "贵州茅台",
                        "上市日期": date(2001, 8, 28),
                    },
                ]
            )

    snapshot = directory_adapter(ListingConflictDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "600519")
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.listing_date is None
    assert item.metadata_source == "directory_conflict"
    assert item.warnings == [
        "instrument 600519 has conflicting directory metadata"
    ]


@pytest.mark.parametrize("raw", [None, float("nan"), pd.NaT, pd.NA, "<NA>"])
def test_directory_treats_missing_listing_date_values_as_none(raw) -> None:
    class MissingListingDateDirectory(FakeAkShareDirectory):
        def stock_info_sh_name_code(self, *, symbol: str) -> pd.DataFrame:
            frame = super().stock_info_sh_name_code(symbol=symbol)
            frame["上市日期"] = raw
            return frame

    snapshot = directory_adapter(MissingListingDateDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "600519")
    assert item.listing_date is None
    assert not any("invalid listing date" in warning for warning in snapshot.warnings)


@pytest.mark.parametrize("raw", ["2001-08-27", "20010827"])
def test_directory_accepts_exact_listing_date_formats(raw: str) -> None:
    class ExactListingDateDirectory(FakeAkShareDirectory):
        def stock_info_sh_name_code(self, *, symbol: str) -> pd.DataFrame:
            frame = super().stock_info_sh_name_code(symbol=symbol)
            frame["上市日期"] = raw
            return frame

    snapshot = directory_adapter(ExactListingDateDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "600519")
    assert item.listing_date == date(2001, 8, 27)


@pytest.mark.parametrize(
    "raw",
    ["not-a-date", "2001-08-27garbage", "20010827garbage"],
)
def test_directory_discards_invalid_nonempty_listing_date_with_warning(raw: str) -> None:
    class InvalidListingDateDirectory(FakeAkShareDirectory):
        def stock_info_sh_name_code(self, *, symbol: str) -> pd.DataFrame:
            frame = super().stock_info_sh_name_code(symbol=symbol)
            frame["上市日期"] = raw
            return frame

    snapshot = directory_adapter(InvalidListingDateDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "600519")
    assert item.listing_date is None
    assert any(
        "akshare_sh_a_share returned invalid listing date for 600519" in warning
        for warning in snapshot.warnings
    )


def test_directory_keeps_verified_etf_with_unknown_rule_as_observation_only() -> None:
    class UnknownRuleDirectory(FakeAkShareDirectory):
        pass

    snapshot = directory_adapter(
        UnknownRuleDirectory(),
        source=FakeSseFundClassificationSource({"510300": "08"}),
    ).fetch(TRADE_DATE)
    item = next(item for item in snapshot.items if item.symbol == "510300")

    assert item.instrument_type is InstrumentType.ETF
    assert item.settlement_cycle is SettlementCycle.UNKNOWN
    assert item.warnings == ["ETF trading category is missing or unsupported"]


def test_directory_does_not_guess_exchange_from_code_prefix() -> None:
    class ExchangeAuthorityDirectory(FakeAkShareDirectory):
        def stock_info_sh_name_code(self, *, symbol: str) -> pd.DataFrame:
            self.calls.append(("sh_a", symbol))
            if symbol == "主板A股":
                return pd.DataFrame([{"证券代码": "000002", "证券简称": "目录权威"}])
            return pd.DataFrame(columns=["证券代码", "证券简称"])

        def stock_info_sz_name_code(self, *, symbol: str) -> pd.DataFrame:
            self.calls.append(("sz_a", symbol))
            return pd.DataFrame(columns=["A股代码", "A股简称"])

    snapshot = directory_adapter(ExchangeAuthorityDirectory()).fetch(TRADE_DATE)

    assert next(item for item in snapshot.items if item.symbol == "000002").exchange is Exchange.SH


def test_directory_keeps_cross_source_conflict_as_unknown_with_warning() -> None:
    class ConflictDirectory(FakeAkShareDirectory):
        def stock_info_sz_name_code(self, *, symbol: str) -> pd.DataFrame:
            self.calls.append(("sz_a", symbol))
            return pd.DataFrame([{"A股代码": "600519", "A股简称": "冲突名称"}])

    snapshot = directory_adapter(ConflictDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "600519")
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.exchange is None
    assert item.settlement_cycle is SettlementCycle.UNKNOWN
    assert item.warnings == ["instrument 600519 has conflicting directory metadata"]
    assert "instrument 600519 has conflicting directory metadata" in snapshot.warnings


def test_directory_rejects_conflicting_szse_etf_settlement_categories() -> None:
    class SettlementConflictDirectory(FakeAkShareDirectory):
        def fund_etf_scale_szse(self) -> pd.DataFrame:
            self.calls.append(("szse_etf", None))
            return pd.DataFrame(
                [
                    {
                        "基金代码": "159915",
                        "基金简称": "创业板ETF",
                        "基金类别": "ETF",
                        "投资类别": "股票基金",
                        "上市日期": date(2011, 12, 9),
                    },
                    {
                        "基金代码": "159915",
                        "基金简称": "创业板ETF",
                        "基金类别": "ETF",
                        "投资类别": "债券基金",
                        "上市日期": date(2011, 12, 9),
                    },
                ]
            )

    snapshot = directory_adapter(SettlementConflictDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "159915")
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.exchange is None
    assert item.settlement_cycle is SettlementCycle.UNKNOWN
    assert item.warnings == [
        "instrument 159915 has conflicting directory metadata"
    ]


def test_directory_keeps_etf_name_mismatch_as_unknown_with_warning() -> None:
    class NameMismatchDirectory(FakeAkShareDirectory):
        def fund_etf_scale_sse(self, *, date: str) -> pd.DataFrame:
            self.calls.append(("sse_etf", date))
            return pd.DataFrame(
                [
                    {
                        "基金代码": "510300",
                        "基金简称": "冲突ETF名称",
                        "ETF类型": "跨市",
                        "统计日期": TRADE_DATE,
                    }
                ]
            )

    snapshot = directory_adapter(NameMismatchDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "510300")
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.exchange is None
    assert item.settlement_cycle is SettlementCycle.UNKNOWN
    assert item.warnings == ["ETF 510300 has conflicting directory names"]


def test_directory_accepts_etf_manager_suffix_name_variant() -> None:
    class ManagerSuffixDirectory(FakeAkShareDirectory):
        def fund_etf_spot_em(self) -> pd.DataFrame:
            self.calls.append(("etf_spot", None))
            return pd.DataFrame(
                [
                    {"代码": "510300", "名称": "沪深300ETF华泰柏瑞"},
                    {"代码": "159915", "名称": "创业板ETF"},
                ]
            )

    snapshot = directory_adapter(ManagerSuffixDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "510300")
    assert item.name == "沪深300ETF华泰柏瑞"
    assert item.instrument_type is InstrumentType.ETF
    assert item.settlement_cycle is SettlementCycle.T1
    assert item.warnings == []


def test_directory_accepts_topic_short_name_and_uses_spot_full_name() -> None:
    item = etf_name_variant_metadata()

    assert item.name == "半导体ETF国联安"
    assert item.exchange is Exchange.SH
    assert item.instrument_type is InstrumentType.ETF
    assert item.settlement_cycle is SettlementCycle.T1
    assert item.warnings == []


def test_directory_rejects_extra_topic_text_before_etf_suffix() -> None:
    class TopicMismatchDirectory(FakeAkShareDirectory):
        def fund_etf_spot_em(self) -> pd.DataFrame:
            self.calls.append(("etf_spot", None))
            return pd.DataFrame(
                [
                    {"代码": "510300", "名称": "沪深300指数ETF"},
                    {"代码": "159915", "名称": "创业板ETF"},
                ]
            )

    snapshot = directory_adapter(TopicMismatchDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "510300")
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.settlement_cycle is SettlementCycle.UNKNOWN
    assert item.warnings == ["ETF 510300 has conflicting directory names"]


def test_directory_keeps_valid_code_with_missing_name_as_unknown() -> None:
    class MissingNameDirectory(FakeAkShareDirectory):
        def stock_info_sz_name_code(self, *, symbol: str) -> pd.DataFrame:
            self.calls.append(("sz_a", symbol))
            return pd.DataFrame([{"A股代码": "000001", "A股简称": ""}])

    snapshot = directory_adapter(MissingNameDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "000001")
    assert item.name == "000001"
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.warnings == ["akshare_sz_a_share returned a missing name for 000001"]


def test_directory_keeps_etf_absent_from_spot_as_unknown() -> None:
    class AbsentSpotDirectory(FakeAkShareDirectory):
        def fund_etf_spot_em(self) -> pd.DataFrame:
            self.calls.append(("etf_spot", None))
            return pd.DataFrame([{"代码": "159915", "名称": "创业板ETF"}])

    snapshot = directory_adapter(AbsentSpotDirectory()).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "510300")
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.warnings == [
        "ETF 510300 cannot be verified by the public ETF directory"
    ]
    assert snapshot.source_item_counts["akshare_sse_etf"] == 1


def test_directory_sanitizes_provider_failure() -> None:
    secret = "secret-api-key-value"

    class FailingDirectory(FakeAkShareDirectory):
        def stock_info_sh_name_code(self, *, symbol: str) -> pd.DataFrame:
            raise RuntimeError(f"apikey={secret} provider unavailable")

    adapter = directory_adapter(FailingDirectory())

    with pytest.raises(InstrumentDirectoryProviderError) as captured:
        adapter.fetch(TRADE_DATE)

    assert secret not in str(captured.value)
    assert "[redacted]" in str(captured.value)


def test_directory_wraps_normalization_contract_failure() -> None:
    class MalformedDirectory(FakeAkShareDirectory):
        def stock_info_sz_name_code(self, *, symbol: str) -> object:
            self.calls.append(("sz_a", symbol))
            return object()

    adapter = directory_adapter(MalformedDirectory())

    with pytest.raises(InstrumentDirectoryProviderError, match="directory mapping failed"):
        adapter.fetch(TRADE_DATE)


def test_sse_fund_classification_source_reads_exact_fund_list_contract() -> None:
    calls: list[tuple[str, dict[str, str], float]] = []

    def transport(url, headers, timeout):  # noqa: ANN001
        calls.append((url, headers, timeout))
        return {
            "result": [
                {
                    "fundCode": "510300",
                    "subClass": "03",
                    "fundName": "沪深300ETF",
                    "listingDate": "20120528",
                },
                {
                    "fundCode": "511010",
                    "subClass": "02",
                    "fundName": "国债ETF",
                    "listingDate": "20091215",
                },
            ]
        }

    result = SseFundClassificationSource(transport=transport).fetch_metadata()

    assert result.subclasses == {"510300": "03", "511010": "02"}
    assert result.listing_dates == {
        "510300": date(2012, 5, 28),
        "511010": date(2009, 12, 15),
    }
    assert len(calls) == 1
    assert "query.sse.com.cn/commonSoaQuery.do" in calls[0][0]
    assert "sqlId=FUND_LIST" in calls[0][0]
    assert "isPagination=true" in calls[0][0]
    assert "pageHelp.pageSize=10000" in calls[0][0]
    assert "fundType=00" in calls[0][0]
    assert "subClass=01%2C02%2C03" in calls[0][0]
    assert calls[0][1]["Referer"] == "https://www.sse.com.cn/"


def test_sse_fund_classification_source_isolates_conflicting_subclasses() -> None:
    source = SseFundClassificationSource(
        transport=lambda *_args: {
            "result": [
                {"fundCode": "510300", "subClass": "03"},
                {"fundCode": "510300", "subClass": "04"},
            ]
        }
    )

    assert source.fetch() == {"510300": "__conflict__"}


def test_sse_fund_classification_source_isolates_conflicting_listing_dates() -> None:
    source = SseFundClassificationSource(
        transport=lambda *_args: {
            "result": [
                {
                    "fundCode": "510300",
                    "subClass": "03",
                    "listingDate": "20120528",
                },
                {
                    "fundCode": "510300",
                    "subClass": "03",
                    "listingDate": "20120529",
                },
            ]
        }
    )

    result = source.fetch_metadata()

    assert result.subclasses == {"510300": "03"}
    assert result.listing_dates == {}
    assert result.listing_date_conflicts == frozenset({"510300"})


def test_sse_etf_conflicting_listing_dates_degrade_directory_metadata() -> None:
    snapshot = directory_adapter(
        FakeAkShareDirectory(),
        source=FakeSseFundClassificationSource(
            listing_dates={},
            listing_date_conflicts=frozenset({"510300"}),
        ),
    ).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "510300")
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.listing_date is None
    assert item.metadata_source == "directory_conflict"
    assert item.warnings == [
        "instrument 510300 has conflicting directory listing dates"
    ]


def test_sse_etf_conflicting_detailed_classification_remains_watch_only() -> None:
    snapshot = directory_adapter(
        FakeAkShareDirectory(),
        source=FakeSseFundClassificationSource({"510300": "__conflict__"}),
    ).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "510300")
    assert item.instrument_type is InstrumentType.ETF
    assert item.settlement_cycle is SettlementCycle.UNKNOWN
    assert item.warnings == ["SSE FUND_LIST contains conflicting subClass"]


def test_sse_fund_classification_source_fails_safely() -> None:
    secret = "raw-response-secret"

    def failing_transport(*_args):  # noqa: ANN002
        raise RuntimeError(f"response={secret}")

    with pytest.raises(InstrumentDirectoryProviderError) as captured:
        SseFundClassificationSource(transport=failing_transport).fetch()

    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None


def test_directory_degrades_only_sse_etf_rules_when_classification_fails() -> None:
    class FailingClassificationSource:
        def fetch(self):  # noqa: ANN201
            raise InstrumentDirectoryProviderError("classification unavailable")

    snapshot = directory_adapter(
        FakeAkShareDirectory(), source=FailingClassificationSource()
    ).fetch(TRADE_DATE)

    by_symbol = {item.symbol: item for item in snapshot.items}
    assert by_symbol["600519"].instrument_type is InstrumentType.A_SHARE
    assert by_symbol["000001"].instrument_type is InstrumentType.A_SHARE
    assert by_symbol["159915"].settlement_cycle is SettlementCycle.T1
    assert by_symbol["510300"].instrument_type is InstrumentType.ETF
    assert by_symbol["510300"].settlement_cycle is SettlementCycle.UNKNOWN
    assert any(
        "SSE fund classification unavailable" in warning
        for warning in snapshot.warnings
    )


def test_sse_etf_missing_detailed_classification_remains_watch_only() -> None:
    snapshot = directory_adapter(
        FakeAkShareDirectory(),
        source=FakeSseFundClassificationSource({}),
    ).fetch(TRADE_DATE)

    item = next(item for item in snapshot.items if item.symbol == "510300")
    assert item.settlement_cycle is SettlementCycle.UNKNOWN
    assert item.warnings == ["ETF trading category is missing or unsupported"]
