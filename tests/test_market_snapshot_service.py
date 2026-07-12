import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
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
    assert created.snapshot.data_time == OLDER_DATA_TIME
    assert created.quotes["600000"].status is QuoteStatus.OK


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
    warning = "api_key=supersecret, Bearer abc, /tmp/private.db"
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
