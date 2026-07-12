from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.market.models import (
    MarketInputSnapshot,
    QuoteSnapshot,
    QuoteStatus,
)
from quantitative_trading.market.repository import (
    MarketInputSnapshotRepository,
    QuoteSnapshotRepository,
)
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.universe.models import (
    UniverseSnapshot,
    UniverseSnapshotStatus,
)
from quantitative_trading.universe.repository import UniverseSnapshotRepository


DATA_TIME = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)
FETCHED_AT = datetime(2026, 7, 12, 6, 0, 5, tzinfo=UTC)


@pytest.fixture
def connection(tmp_path) -> Iterator:
    settings = Settings(database_path=tmp_path / "market.db")
    with connect(settings) as migrated_connection:
        migrate(migrated_connection)
        yield migrated_connection


def quote_snapshot(status: QuoteStatus) -> QuoteSnapshot:
    requires_quote = status is not QuoteStatus.FAILED
    return QuoteSnapshot(
        symbol="600000",
        name="Pufa Bank" if requires_quote else "",
        current_price=10.5 if requires_quote else None,
        change_pct=1.2 if requires_quote else None,
        data_time=DATA_TIME if requires_quote else None,
        fetched_at=FETCHED_AT,
        source="akshare",
        status=status,
        warning="" if status is QuoteStatus.OK else f"{status.value} quote",
    )


def universe_snapshot() -> UniverseSnapshot:
    return UniverseSnapshot(
        created_at=FETCHED_AT,
        status=UniverseSnapshotStatus.OK,
        warnings=[],
        members=[],
    )


@pytest.mark.parametrize("status", list(QuoteStatus))
def test_quote_snapshot_save_get_and_latest_round_trip_all_statuses(
    connection,
    status: QuoteStatus,
) -> None:
    repository = QuoteSnapshotRepository(connection)
    snapshot = quote_snapshot(status)

    snapshot_id = repository.save(snapshot)

    assert repository.get(snapshot_id) == snapshot
    assert repository.latest_for_symbol(snapshot.symbol) == snapshot


def test_quote_snapshot_get_and_latest_return_none_when_absent(connection) -> None:
    repository = QuoteSnapshotRepository(connection)

    assert repository.get(999) is None
    assert repository.latest_for_symbol("600000") is None


def test_quote_snapshot_latest_for_symbol_uses_highest_id(connection) -> None:
    repository = QuoteSnapshotRepository(connection)
    first = quote_snapshot(QuoteStatus.OK)
    second = first.model_copy(
        update={
            "current_price": 10.2,
            "data_time": datetime(2026, 7, 12, 5, 59, tzinfo=UTC),
        }
    )

    repository.save(first)
    repository.save(second)

    assert repository.latest_for_symbol("600000") == second


def test_market_input_snapshot_save_get_and_latest_round_trip_full_payload(
    connection,
) -> None:
    universe_snapshot_id = UniverseSnapshotRepository(connection).save(
        universe_snapshot()
    )
    quote_id = QuoteSnapshotRepository(connection).save(
        quote_snapshot(QuoteStatus.OK)
    )
    market_snapshot = MarketInputSnapshot(
        universe_snapshot_id=universe_snapshot_id,
        quote_snapshot_refs={"600000": quote_id},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        data_time=DATA_TIME,
        fetched_at=FETCHED_AT,
        warnings=[],
    )
    repository = MarketInputSnapshotRepository(connection)

    snapshot_id = repository.save(market_snapshot)

    assert repository.get(snapshot_id) == market_snapshot
    assert repository.latest() == market_snapshot


def test_market_input_snapshot_get_and_latest_return_none_when_absent(
    connection,
) -> None:
    repository = MarketInputSnapshotRepository(connection)

    assert repository.get(999) is None
    assert repository.latest() is None


def test_market_repositories_can_join_caller_owned_transaction(connection) -> None:
    universe_snapshot_id = UniverseSnapshotRepository(connection).save(
        universe_snapshot()
    )
    quote_repository = QuoteSnapshotRepository(connection)
    market_repository = MarketInputSnapshotRepository(connection)

    quote_id = quote_repository.save(quote_snapshot(QuoteStatus.OK), commit=False)
    market_repository.save(
        MarketInputSnapshot(
            universe_snapshot_id=universe_snapshot_id,
            quote_snapshot_refs={"600000": quote_id},
            history_snapshot_refs={},
            money_flow_snapshot_refs={},
            intraday_strength_snapshot_refs={},
            data_time=DATA_TIME,
            fetched_at=FETCHED_AT,
            warnings=[],
        ),
        commit=False,
    )
    connection.rollback()

    assert quote_repository.get(quote_id) is None
    assert market_repository.latest() is None
