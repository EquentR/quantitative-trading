from datetime import UTC, date, datetime, time, timedelta

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.instrument.adapters import (
    InstrumentDirectoryProviderError,
    InstrumentDirectorySnapshot,
)
from quantitative_trading.instrument.directory import (
    InstrumentDirectoryService,
    InstrumentDirectoryUnavailableError,
    directory_summary_warnings,
    latest_completed_directory_trade_date,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import (
    InstrumentCatalogState,
    InstrumentCatalogStateRepository,
    InstrumentRepository,
)
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)
TRADE_DATE = date(2026, 7, 15)
SOURCES = ("source_sh", "source_sz")


def metadata(symbol: str = "600519", name: str = "贵州茅台") -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        name=name,
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        metadata_source="source_sh",
        metadata_checked_at=NOW,
        rule_version="instrument-trading-rules-v1",
    )


class FakeDirectoryAdapter:
    sources = SOURCES

    def __init__(
        self,
        *,
        error: Exception | None = None,
        source_item_counts: dict[str, int] | None = None,
        items: list[InstrumentMetadata] | None = None,
    ) -> None:
        self.error = error
        self.source_item_counts = source_item_counts
        self.items = items
        self.calls: list[date] = []

    def fetch(self, trade_date: date) -> InstrumentDirectorySnapshot:
        self.calls.append(trade_date)
        if self.error is not None:
            raise self.error
        return InstrumentDirectorySnapshot(
            items=self.items if self.items is not None else [metadata()],
            source_trade_dates={source: trade_date for source in self.sources},
            warnings=[],
            source_item_counts=(
                self.source_item_counts
                if self.source_item_counts is not None
                else {source: 1 for source in self.sources}
            ),
        )


def build_service(connection, adapter: FakeDirectoryAdapter) -> InstrumentDirectoryService:
    return InstrumentDirectoryService(
        InstrumentRepository(connection),
        InstrumentCatalogStateRepository(connection),
        adapter,
        now=lambda: NOW,
    )


def test_directory_refreshes_only_once_per_shanghai_natural_day(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "directory.db")
    adapter = FakeDirectoryAdapter()
    with connect(settings) as connection:
        migrate(connection)
        service = build_service(connection, adapter)

        first = service.ensure_current(TRADE_DATE)
        second = service.ensure_current(TRADE_DATE)

    assert [item.symbol for item in first.items] == ["600519"]
    assert second.items == first.items
    assert adapter.calls == [TRADE_DATE]


def test_successful_refresh_warnings_survive_same_day_cached_reads(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "cached-warning.db")

    class WarningAdapter(FakeDirectoryAdapter):
        def fetch(self, trade_date: date) -> InstrumentDirectorySnapshot:
            snapshot = super().fetch(trade_date)
            return InstrumentDirectorySnapshot(
                items=snapshot.items,
                source_trade_dates=snapshot.source_trade_dates,
                warnings=["directory conflict was excluded"],
                source_item_counts=snapshot.source_item_counts,
            )

    with connect(settings) as connection:
        migrate(connection)
        service = build_service(connection, WarningAdapter())
        service.ensure_current(TRADE_DATE)
        cached = service.ensure_current(TRADE_DATE)

    assert cached.warnings == ["directory conflict was excluded"]


def test_refresh_failure_uses_stale_catalog_and_records_safe_error(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "stale.db")
    adapter = FakeDirectoryAdapter(
        error=InstrumentDirectoryProviderError(
            "apikey=secret-value instrument provider unavailable"
        )
    )
    yesterday = NOW - timedelta(days=1)
    with connect(settings) as connection:
        migrate(connection)
        instruments = InstrumentRepository(connection)
        states = InstrumentCatalogStateRepository(connection)
        instruments.replace_catalog([metadata()])
        for source in SOURCES:
            states.save(
                InstrumentCatalogState(
                    source=source,
                    last_attempt_at=yesterday,
                    last_success_at=yesterday,
                    data_trade_date=TRADE_DATE - timedelta(days=1),
                    status="complete",
                    updated_at=yesterday,
                )
            )
        service = build_service(connection, adapter)

        snapshot = service.ensure_current(TRADE_DATE)
        state = states.get(SOURCES[0])

    assert [item.symbol for item in snapshot.items] == ["600519"]
    assert snapshot.warnings == ["instrument directory refresh failed; using stale catalog"]
    assert state is not None
    assert state.status.value == "stale"
    assert state.last_success_at == yesterday
    assert "secret-value" not in state.last_error


def test_first_refresh_failure_raises_stable_unavailable_error(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "failed.db")
    adapter = FakeDirectoryAdapter(error=RuntimeError("offline"))
    with connect(settings) as connection:
        migrate(connection)
        service = build_service(connection, adapter)

        with pytest.raises(InstrumentDirectoryUnavailableError):
            service.ensure_current(TRADE_DATE)

        states = InstrumentCatalogStateRepository(connection)
        assert states.get(SOURCES[0]).status.value == "failed"


def test_empty_search_does_not_refresh_directory(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "empty.db")
    adapter = FakeDirectoryAdapter()
    with connect(settings) as connection:
        migrate(connection)
        service = build_service(connection, adapter)

        result = service.search("   ", trade_date=TRADE_DATE)

    assert result == []
    assert adapter.calls == []


def test_search_refreshes_then_reads_only_local_normalized_catalog(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "search.db")
    adapter = FakeDirectoryAdapter()
    with connect(settings) as connection:
        migrate(connection)
        service = build_service(connection, adapter)

        result = service.search("茅台", trade_date=TRADE_DATE)

    assert [item.symbol for item in result] == ["600519"]
    assert adapter.calls == [TRADE_DATE]


def test_partial_empty_source_uses_stale_catalog_instead_of_deactivating_it(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "partial-empty.db")
    yesterday = NOW - timedelta(days=1)
    adapter = FakeDirectoryAdapter(
        source_item_counts={"source_sh": 1, "source_sz": 0},
        items=[metadata("600000", "new")],
    )
    with connect(settings) as connection:
        migrate(connection)
        instruments = InstrumentRepository(connection)
        states = InstrumentCatalogStateRepository(connection)
        instruments.replace_catalog([metadata("600519", "cached")])
        for source in SOURCES:
            states.save(
                InstrumentCatalogState(
                    source=source,
                    last_attempt_at=yesterday,
                    last_success_at=yesterday,
                    data_trade_date=TRADE_DATE - timedelta(days=1),
                    status="complete",
                    updated_at=yesterday,
                )
            )

        snapshot = build_service(connection, adapter).ensure_current(TRADE_DATE)

        assert [item.symbol for item in snapshot.items] == ["600519"]
        assert instruments.get("600519") is not None
        assert instruments.get("600000") is None
        assert states.get("source_sz").status.value == "stale"


def test_catalog_and_all_source_states_commit_atomically(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "atomic-directory.db")
    adapter = FakeDirectoryAdapter(items=[metadata("600000", "new")])
    with connect(settings) as connection:
        migrate(connection)
        instruments = InstrumentRepository(connection)
        instruments.replace_catalog([metadata("600519", "cached")])
        connection.execute(
            """
            CREATE TRIGGER fail_second_catalog_state
            BEFORE INSERT ON instrument_catalog_state
            WHEN NEW.source = 'source_sz'
            BEGIN
              SELECT RAISE(ABORT, 'synthetic state failure');
            END
            """
        )

        with pytest.raises(Exception, match="synthetic state failure"):
            build_service(connection, adapter).ensure_current(TRADE_DATE)

        assert instruments.get("600519") is not None
        assert instruments.get("600000") is None
        assert InstrumentCatalogStateRepository(connection).get("source_sh") is None


def test_directory_trade_date_uses_previous_session_until_close_data_is_ready() -> None:
    calendar = XSHGTradingCalendar()
    before_close = datetime.combine(
        TRADE_DATE,
        time(10, 0),
        tzinfo=calendar.timezone,
    )
    after_close = datetime.combine(
        TRADE_DATE,
        time(17, 0),
        tzinfo=calendar.timezone,
    )

    assert latest_completed_directory_trade_date(before_close, calendar) == date(
        2026, 7, 14
    )
    assert latest_completed_directory_trade_date(after_close, calendar) == TRADE_DATE
def test_directory_summary_warnings_excludes_symbol_details_and_deduplicates() -> None:
    assert directory_summary_warnings(
        [
            "ETF 510010 has conflicting directory names",
            "instrument directory refresh failed; using stale catalog",
            "instrument directory refresh failed; using stale catalog",
            "SSE classification unavailable",
        ]
    ) == [
        "instrument directory refresh failed; using stale catalog",
        "SSE classification unavailable",
    ]
