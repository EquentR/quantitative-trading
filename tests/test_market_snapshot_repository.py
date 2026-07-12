from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone

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
FETCHED_AT_CST = datetime(
    2026,
    7,
    12,
    14,
    0,
    5,
    tzinfo=timezone(timedelta(hours=8)),
)


@pytest.fixture
def connection(tmp_path) -> Iterator:
    settings = Settings(database_path=tmp_path / "market.db")
    with connect(settings) as migrated_connection:
        migrate(migrated_connection)
        yield migrated_connection


def quote_snapshot(
    status: QuoteStatus,
    *,
    symbol: str = "600000",
) -> QuoteSnapshot:
    requires_quote = status is not QuoteStatus.FAILED
    return QuoteSnapshot(
        symbol=symbol,
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


def test_quote_snapshot_latest_for_symbol_filters_interleaved_rows_by_highest_id(
    connection,
) -> None:
    repository = QuoteSnapshotRepository(connection)
    first_pufa = quote_snapshot(QuoteStatus.OK)
    ping_an = quote_snapshot(QuoteStatus.OK, symbol="000001").model_copy(
        update={"name": "Ping An Bank", "current_price": 12.3}
    )
    latest_pufa = first_pufa.model_copy(
        update={
            "current_price": 10.2,
            "data_time": datetime(2026, 7, 12, 5, 59, tzinfo=UTC),
        }
    )

    repository.save(first_pufa)
    repository.save(ping_an)
    repository.save(latest_pufa)

    assert repository.latest_for_symbol("600000") == latest_pufa
    assert repository.latest_for_symbol("000001") == ping_an


def test_quote_snapshot_save_commits_by_default_for_other_connections(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "market.db")
    snapshot = quote_snapshot(QuoteStatus.OK)

    with connect(settings) as writer:
        migrate(writer)
        snapshot_id = QuoteSnapshotRepository(writer).save(snapshot)

    with connect(settings) as reader:
        assert QuoteSnapshotRepository(reader).get(snapshot_id) == snapshot


def test_market_input_snapshot_save_get_and_latest_round_trip_full_payload(
    connection,
) -> None:
    universe_snapshot_id = UniverseSnapshotRepository(connection).save(
        universe_snapshot()
    )
    quote_id = QuoteSnapshotRepository(connection).save(
        quote_snapshot(QuoteStatus.FAILED)
    )
    market_snapshot = MarketInputSnapshot(
        universe_snapshot_id=universe_snapshot_id,
        quote_snapshot_refs={"600000": quote_id},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        data_time=None,
        fetched_at=FETCHED_AT_CST,
        warnings=["全部行情获取失败"],
    )
    repository = MarketInputSnapshotRepository(connection)

    snapshot_id = repository.save(market_snapshot)
    restored = repository.get(snapshot_id)

    assert restored == market_snapshot
    assert restored is not None
    assert restored.data_time is None
    assert restored.fetched_at.utcoffset() == timedelta(hours=8)
    assert restored.warnings == ["全部行情获取失败"]
    assert repository.latest() == market_snapshot


def test_market_input_snapshot_save_commits_by_default_for_other_connections(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "market.db")

    with connect(settings) as writer:
        migrate(writer)
        universe_snapshot_id = UniverseSnapshotRepository(writer).save(
            universe_snapshot()
        )
        quote_id = QuoteSnapshotRepository(writer).save(
            quote_snapshot(QuoteStatus.OK)
        )
        snapshot = MarketInputSnapshot(
            universe_snapshot_id=universe_snapshot_id,
            quote_snapshot_refs={"600000": quote_id},
            history_snapshot_refs={},
            money_flow_snapshot_refs={},
            intraday_strength_snapshot_refs={},
            data_time=DATA_TIME,
            fetched_at=FETCHED_AT,
            warnings=[],
        )
        snapshot_id = MarketInputSnapshotRepository(writer).save(snapshot)

    with connect(settings) as reader:
        assert MarketInputSnapshotRepository(reader).get(snapshot_id) == snapshot


def test_market_input_snapshot_get_and_latest_return_none_when_absent(
    connection,
) -> None:
    repository = MarketInputSnapshotRepository(connection)

    assert repository.get(999) is None
    assert repository.latest() is None


def test_market_input_snapshot_latest_returns_highest_id(connection) -> None:
    universe_snapshot_id = UniverseSnapshotRepository(connection).save(
        universe_snapshot()
    )
    quote_repository = QuoteSnapshotRepository(connection)
    first_quote_id = quote_repository.save(quote_snapshot(QuoteStatus.OK))
    second_quote_id = quote_repository.save(
        quote_snapshot(QuoteStatus.OK, symbol="000001")
    )
    first = MarketInputSnapshot(
        universe_snapshot_id=universe_snapshot_id,
        quote_snapshot_refs={"600000": first_quote_id},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        data_time=DATA_TIME,
        fetched_at=FETCHED_AT,
        warnings=["first"],
    )
    second = MarketInputSnapshot(
        universe_snapshot_id=universe_snapshot_id,
        quote_snapshot_refs={"000001": second_quote_id},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        data_time=datetime(2026, 7, 12, 5, 59, tzinfo=UTC),
        fetched_at=FETCHED_AT,
        warnings=["second"],
    )
    repository = MarketInputSnapshotRepository(connection)

    repository.save(first)
    repository.save(second)

    assert repository.latest() == second


def test_market_input_save_rejects_missing_quote_reference_without_inserting(
    connection,
) -> None:
    universe_snapshot_id = UniverseSnapshotRepository(connection).save(
        universe_snapshot()
    )
    repository = MarketInputSnapshotRepository(connection)
    snapshot = MarketInputSnapshot(
        universe_snapshot_id=universe_snapshot_id,
        quote_snapshot_refs={"600000": 999},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        data_time=DATA_TIME,
        fetched_at=FETCHED_AT,
        warnings=[],
    )

    with pytest.raises(ValueError, match="invalid quote snapshot reference"):
        repository.save(snapshot)

    assert repository.latest() is None


def test_market_input_save_rejects_symbol_mismatched_quote_without_inserting(
    connection,
) -> None:
    universe_snapshot_id = UniverseSnapshotRepository(connection).save(
        universe_snapshot()
    )
    quote_id = QuoteSnapshotRepository(connection).save(
        quote_snapshot(QuoteStatus.OK, symbol="000001")
    )
    repository = MarketInputSnapshotRepository(connection)
    snapshot = MarketInputSnapshot(
        universe_snapshot_id=universe_snapshot_id,
        quote_snapshot_refs={"600000": quote_id},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        data_time=DATA_TIME,
        fetched_at=FETCHED_AT,
        warnings=[],
    )

    with pytest.raises(ValueError, match="invalid quote snapshot reference"):
        repository.save(snapshot)

    assert repository.latest() is None


def test_market_input_save_accepts_uncommitted_matching_quote_reference(
    connection,
) -> None:
    universe_snapshot_id = UniverseSnapshotRepository(connection).save(
        universe_snapshot()
    )
    quote_id = QuoteSnapshotRepository(connection).save(
        quote_snapshot(QuoteStatus.OK), commit=False
    )
    repository = MarketInputSnapshotRepository(connection)
    snapshot = MarketInputSnapshot(
        universe_snapshot_id=universe_snapshot_id,
        quote_snapshot_refs={"600000": quote_id},
        history_snapshot_refs={},
        money_flow_snapshot_refs={},
        intraday_strength_snapshot_refs={},
        data_time=DATA_TIME,
        fetched_at=FETCHED_AT,
        warnings=[],
    )

    snapshot_id = repository.save(snapshot, commit=False)

    assert repository.get(snapshot_id) == snapshot


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
