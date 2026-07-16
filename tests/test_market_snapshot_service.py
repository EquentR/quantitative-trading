import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    QuoteSnapshot,
    QuoteStatus,
)
from quantitative_trading.market.repository import QuoteSnapshotRepository
from quantitative_trading.market.snapshot_service import MarketSnapshotService
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.universe.repository import UniverseSnapshotRepository
from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedSource
from quantitative_trading.watchlist.repository import WatchPinnedRepository


FETCHED_AT = datetime(2026, 7, 12, 6, 0, 5, tzinfo=UTC)
OLDER_DATA_TIME = FETCHED_AT - timedelta(minutes=2)


class RecordingMarketDataProvider:
    def __init__(self, quotes: dict[str, QuoteSnapshot]) -> None:
        self.quotes = quotes
        self.calls: list[list[str]] = []

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
        self.calls.append(list(symbols))
        return self.quotes


@pytest.fixture
def connection(tmp_path) -> Iterator:
    settings = Settings(database_path=tmp_path / "market-snapshot-service.db")
    with connect(settings) as migrated_connection:
        migrate(migrated_connection)
        yield migrated_connection


@pytest.fixture
def decision_universe(connection) -> None:
    InstrumentRepository(connection).replace_catalog(
        [
            InstrumentMetadata(
                symbol=symbol,
                name=f"Name {symbol}",
                exchange=Exchange.SZ,
                instrument_type=InstrumentType.A_SHARE,
                settlement_cycle=SettlementCycle.T1,
                metadata_source="test-directory",
                metadata_checked_at=FETCHED_AT,
                rule_version="test-rules-v1",
            )
            for symbol in ("000001", "000002", "600000")
        ]
    )
    PositionRepository(connection).add(
        PositionInput(
            symbol="600000",
            name="Pufa Bank",
            quantity=1000,
            available_quantity=800,
            cost_price=9.5,
            opened_at=date(2026, 7, 6),
        ),
        now=FETCHED_AT,
    )
    repository = WatchPinnedRepository(connection)
    repository.upsert(
        WatchPinnedInput(
            symbol="000001",
            name="Ping An Bank",
            rank=1,
            plan_enabled=True,
        ),
        source=WatchPinnedSource.MANUAL,
        now=FETCHED_AT,
    )
    repository.upsert(
        WatchPinnedInput(
            symbol="000002",
            name="Vanke A",
            rank=2,
            plan_enabled=False,
        ),
        source=WatchPinnedSource.MANUAL,
        now=FETCHED_AT,
    )


def ok_quote(symbol: str, *, data_time: datetime = FETCHED_AT) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        name=f"Name {symbol}",
        current_price=10.5,
        change_pct=1.2,
        data_time=data_time,
        fetched_at=FETCHED_AT,
        source="fake_provider",
        status=QuoteStatus.OK,
    )


def failed_quote(symbol: str, *, data_time: datetime) -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol,
        data_time=data_time,
        fetched_at=FETCHED_AT,
        source="fake_provider",
        status=QuoteStatus.FAILED,
        warning="provider quote failed",
    )


def test_capture_fetches_stable_sorted_decision_enabled_quotes_and_persists_references(
    connection,
    decision_universe,
) -> None:
    provider = RecordingMarketDataProvider(
        {
            "000001": ok_quote("000001", data_time=OLDER_DATA_TIME),
            "600000": ok_quote("600000"),
        }
    )

    created = MarketSnapshotService(
        connection,
        provider,
        now=FETCHED_AT,
    ).capture()

    assert provider.calls == [["000001", "600000"]]
    assert created.snapshot_id > 0
    assert created.snapshot.universe_snapshot_id > 0
    assert set(created.snapshot.quote_snapshot_refs) == {"000001", "600000"}
    assert set(created.snapshot.instrument_metadata) == {"000001", "600000"}
    assert all(
        created.snapshot.dataset_quality[symbol][CaptureDataset.QUOTE].status
        is CaptureResultStatus.COMPLETE
        for symbol in ("000001", "600000")
    )
    assert created.snapshot.data_time == OLDER_DATA_TIME
    assert created.quotes["600000"].status is QuoteStatus.OK


def test_capture_routes_a_share_and_etf_quotes_and_skips_unknown_provider_calls(
    connection,
) -> None:
    InstrumentRepository(connection).replace_catalog(
        [
            InstrumentMetadata(
                symbol="600000",
                name="A share",
                exchange=Exchange.SH,
                instrument_type=InstrumentType.A_SHARE,
                settlement_cycle=SettlementCycle.T1,
                price_limit_ratio=0.1,
                metadata_source="test-directory",
                metadata_checked_at=FETCHED_AT,
                rule_version="test-rules-v1",
            ),
            InstrumentMetadata(
                symbol="510300",
                name="ETF",
                exchange=Exchange.SH,
                instrument_type=InstrumentType.ETF,
                settlement_cycle=SettlementCycle.T1,
                price_limit_ratio=0.1,
                metadata_source="test-directory",
                metadata_checked_at=FETCHED_AT,
                rule_version="test-rules-v1",
            ),
            InstrumentMetadata(
                symbol="900001",
                name="Unknown",
                exchange=None,
                instrument_type=InstrumentType.UNKNOWN,
                settlement_cycle=SettlementCycle.UNKNOWN,
                metadata_source="legacy-unverified",
                metadata_checked_at=FETCHED_AT,
                rule_version="test-rules-v1",
            ),
        ]
    )
    positions = PositionRepository(connection)
    for symbol in ("600000", "900001"):
        positions.add(
            PositionInput(
                symbol=symbol,
                name=symbol,
                quantity=100,
                available_quantity=100,
                cost_price=10,
                opened_at=date(2026, 7, 6),
            ),
            now=FETCHED_AT,
        )
    WatchPinnedRepository(connection).upsert(
        WatchPinnedInput(
            symbol="510300",
            name="ETF",
            rank=1,
            plan_enabled=True,
        ),
        source=WatchPinnedSource.MANUAL,
        now=FETCHED_AT,
    )
    a_share_provider = RecordingMarketDataProvider({"600000": ok_quote("600000")})
    etf_provider = RecordingMarketDataProvider({"510300": ok_quote("510300")})

    created = MarketSnapshotService(
        connection,
        a_share_provider,
        etf_provider=etf_provider,
        now=FETCHED_AT,
    ).capture()

    assert a_share_provider.calls == [["600000"]]
    assert etf_provider.calls == [["510300"]]
    assert created.quotes["900001"].status is QuoteStatus.FAILED
    assert (
        created.snapshot.dataset_quality["510300"][CaptureDataset.MONEY_FLOW].status
        is CaptureResultStatus.NOT_APPLICABLE
    )
    assert (
        created.snapshot.dataset_quality["900001"][CaptureDataset.QUOTE].status
        is CaptureResultStatus.FAILED
    )
    assert any("900001" in warning and "证券类型未知" in warning for warning in created.snapshot.warnings)


def test_capture_data_time_ignores_older_failed_quote(
    connection,
    decision_universe,
) -> None:
    provider = RecordingMarketDataProvider(
        {
            "000001": failed_quote("000001", data_time=OLDER_DATA_TIME),
            "600000": ok_quote("600000"),
        }
    )

    created = MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()

    assert created.snapshot.data_time == FETCHED_AT


def test_capture_data_time_is_none_when_all_quotes_failed(
    connection,
    decision_universe,
) -> None:
    provider = RecordingMarketDataProvider(
        {
            "000001": failed_quote("000001", data_time=OLDER_DATA_TIME),
            "600000": failed_quote("600000", data_time=FETCHED_AT),
        }
    )

    created = MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()

    assert created.snapshot.data_time is None


def test_capture_persists_failed_quote_when_provider_omits_requested_symbol(
    connection,
    decision_universe,
) -> None:
    provider = RecordingMarketDataProvider({"000001": ok_quote("000001")})

    created = MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()
    failed_quote = QuoteSnapshotRepository(connection).get(
        created.snapshot.quote_snapshot_refs["600000"]
    )
    assert failed_quote is not None
    assert failed_quote.status is QuoteStatus.FAILED
    assert failed_quote.source == "market_snapshot_service"
    assert "600000" in failed_quote.warning


def test_capture_ignores_extra_provider_symbol_and_records_collection_warning(
    connection,
    decision_universe,
) -> None:
    provider = RecordingMarketDataProvider(
        {
            "000001": ok_quote("000001"),
            "600000": ok_quote("600000"),
            "000002": ok_quote("000002"),
        }
    )

    created = MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()
    assert set(created.quotes) == {"000001", "600000"}
    assert set(created.snapshot.quote_snapshot_refs) == {"000001", "600000"}
    assert any("000002" in warning for warning in created.snapshot.warnings)


def test_capture_sanitizes_provider_exception_before_persistence(
    connection,
    decision_universe,
) -> None:
    secret_text = "api_key=supersecret, Bearer abc, /tmp/private.db"

    class FailingProvider:
        def get_quotes(self, symbols: Sequence[str]) -> dict[str, QuoteSnapshot]:
            raise RuntimeError(secret_text)

    created = MarketSnapshotService(connection, FailingProvider(), now=FETCHED_AT).capture()
    persisted_quotes = [
        QuoteSnapshotRepository(connection).get(snapshot_id)
        for snapshot_id in created.snapshot.quote_snapshot_refs.values()
    ]
    persisted_text = " ".join(
        [*created.snapshot.warnings, *(quote.warning for quote in persisted_quotes if quote)]
    )
    assert "supersecret" not in persisted_text
    assert "Bearer abc" not in persisted_text
    assert "/tmp/private.db" not in persisted_text


def test_capture_empty_decision_enabled_set_skips_provider_and_persists_snapshots(
    connection,
) -> None:
    provider = RecordingMarketDataProvider({})

    created = MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()
    universe = UniverseSnapshotRepository(connection).get(
        created.snapshot.universe_snapshot_id
    )

    assert provider.calls == []
    assert universe is not None
    assert universe.members == []
    assert created.snapshot.quote_snapshot_refs == {}
    assert "无决策启用标的，未调用行情数据源" in created.snapshot.warnings


def test_capture_requests_symbol_once_when_holding_and_enabled_watch_overlap(
    connection,
    decision_universe,
) -> None:
    WatchPinnedRepository(connection).upsert(
        WatchPinnedInput(
            symbol="600000",
            name="Pufa Bank Watch",
            rank=3,
            plan_enabled=True,
        ),
        source=WatchPinnedSource.MANUAL,
        now=FETCHED_AT,
    )
    provider = RecordingMarketDataProvider(
        {"000001": ok_quote("000001"), "600000": ok_quote("600000")}
    )

    MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()

    assert provider.calls == [["000001", "600000"]]
    assert provider.calls[0].count("600000") == 1


def test_capture_replaces_mismatched_provider_quote_with_requested_symbol_failure(
    connection,
    decision_universe,
) -> None:
    provider = RecordingMarketDataProvider(
        {
            "000001": ok_quote("600000"),
            "600000": ok_quote("600000"),
        }
    )

    created = MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()
    mismatch = QuoteSnapshotRepository(connection).get(
        created.snapshot.quote_snapshot_refs["000001"]
    )
    assert mismatch is not None
    assert mismatch.symbol == "000001"
    assert mismatch.status is QuoteStatus.FAILED
    assert any(
        "000001" in warning and "mismatch" in warning
        for warning in created.snapshot.warnings
    )


def test_capture_sanitizes_provider_quote_warnings_before_persistence(
    connection,
    decision_universe,
) -> None:
    warning = "provider response incomplete; api_key=supersecret, Bearer abc, /tmp/private.db"
    provider = RecordingMarketDataProvider(
        {
            "000001": ok_quote("000001").model_copy(
                update={"status": QuoteStatus.PARTIAL, "warning": warning}
            ),
            "600000": ok_quote("600000"),
        }
    )

    created = MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()
    quote = QuoteSnapshotRepository(connection).get(
        created.snapshot.quote_snapshot_refs["000001"]
    )
    assert quote is not None
    aggregate_warning = next(
        warning
        for warning in created.snapshot.warnings
        if "000001" in warning and "partial" in warning
    )
    assert "provider response incomplete" in aggregate_warning
    persisted_text = " ".join([quote.warning, *created.snapshot.warnings])
    assert "supersecret" not in persisted_text
    assert "Bearer abc" not in persisted_text
    assert "/tmp/private.db" not in persisted_text


def test_capture_rolls_back_universe_quote_and_aggregate_writes_on_database_failure(
    connection,
    decision_universe,
) -> None:
    connection.execute(
        """
        CREATE TRIGGER fail_market_input_snapshot_insert
        BEFORE INSERT ON market_input_snapshots
        BEGIN
          SELECT RAISE(ABORT, 'forced aggregate failure');
        END;
        """
    )
    connection.commit()
    provider = RecordingMarketDataProvider(
        {"000001": ok_quote("000001"), "600000": ok_quote("600000")}
    )

    with pytest.raises(sqlite3.IntegrityError, match="forced aggregate failure"):
        MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()

    assert connection.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM quote_snapshots").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM market_input_snapshots").fetchone()[0] == 0


def test_capture_rolls_back_all_writes_with_autocommit_connection() -> None:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    migrate(connection)
    try:
        PositionRepository(connection).add(
            PositionInput(
                symbol="600000",
                name="Pufa Bank",
                quantity=1000,
                available_quantity=800,
                cost_price=9.5,
                opened_at=date(2026, 7, 6),
            ),
            now=FETCHED_AT,
        )
        connection.commit()
        connection.execute(
            """
            CREATE TRIGGER fail_autocommit_market_input_snapshot_insert
            BEFORE INSERT ON market_input_snapshots
            BEGIN
              SELECT RAISE(ABORT, 'forced aggregate failure');
            END;
            """
        )
        provider = RecordingMarketDataProvider({"600000": ok_quote("600000")})

        with pytest.raises(sqlite3.IntegrityError, match="forced aggregate failure"):
            MarketSnapshotService(connection, provider, now=FETCHED_AT).capture()

        assert connection.execute("SELECT COUNT(*) FROM universe_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM quote_snapshots").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM market_input_snapshots").fetchone()[0] == 0
    finally:
        connection.close()
