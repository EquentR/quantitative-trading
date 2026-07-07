from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from quantitative_trading.account.models import (
    AccountSnapshot,
    AccountSnapshotStatus,
    PositionValuation,
    PositionValuationStatus,
)
from quantitative_trading.account.repository import AccountSnapshotRepository
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)
LATER = datetime(2026, 7, 7, 3, 0, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path) -> Iterator[AccountSnapshotRepository]:
    settings = Settings(database_path=tmp_path / "account.db")
    with connect(settings) as connection:
        migrate(connection)
        yield AccountSnapshotRepository(connection)


def position_valuation() -> PositionValuation:
    return PositionValuation(
        symbol="600000",
        name="浦发银行",
        quantity=1000,
        available_quantity=800,
        cost_price=9.5,
        position_cost=9500,
        current_price=10.5,
        market_value=10500,
        floating_pnl=1000,
        floating_pnl_pct=1000 / 9500,
        ledger_updated_at=NOW,
        quote_data_time=NOW,
        quote_fetched_at=NOW,
        status=PositionValuationStatus.OK,
    )


def valued_snapshot(
    *,
    created_at: datetime = NOW,
    total_assets: float = 60500,
) -> AccountSnapshot:
    return AccountSnapshot(
        cash_balance=50000,
        net_principal=50000,
        market_value=10500,
        position_cost=9500,
        floating_pnl=1000,
        floating_pnl_pct=1000 / 9500,
        total_assets=total_assets,
        total_pnl=total_assets - 50000,
        total_pnl_pct=(total_assets - 50000) / 50000,
        position_ratio=10500 / total_assets,
        available_buying_cash=50000,
        positions=[position_valuation()],
        status=AccountSnapshotStatus.OK,
        warnings=[],
        created_at=created_at,
    )


def market_data_unavailable_snapshot() -> AccountSnapshot:
    return AccountSnapshot(
        cash_balance=50000,
        net_principal=50000,
        available_buying_cash=50000,
        positions=[
            PositionValuation(
                symbol="600000",
                name="浦发银行",
                quantity=1000,
                available_quantity=800,
                cost_price=9.5,
                position_cost=9500,
                ledger_updated_at=NOW,
                quote_data_time=None,
                quote_fetched_at=None,
                status=PositionValuationStatus.FAILED,
                warning="quote unavailable",
            )
        ],
        status=AccountSnapshotStatus.MARKET_DATA_UNAVAILABLE,
        warnings=["600000: quote unavailable"],
        created_at=NOW,
    )


def cash_not_initialized_snapshot() -> AccountSnapshot:
    return AccountSnapshot(
        positions=[],
        status=AccountSnapshotStatus.CASH_NOT_INITIALIZED,
        warnings=["cash account not initialized"],
        created_at=NOW,
    )


def persisted_rows(repository: AccountSnapshotRepository) -> list:
    return repository.connection.execute(
        """
        SELECT *
        FROM account_snapshots
        ORDER BY id ASC
        """
    ).fetchall()


def test_latest_returns_none_when_table_is_empty(repository: AccountSnapshotRepository) -> None:
    assert repository.latest() is None


def test_save_then_latest_reads_back_snapshot_from_id_one(
    repository: AccountSnapshotRepository,
) -> None:
    snapshot = valued_snapshot()

    saved_id = repository.save(
        snapshot,
        cash_account_updated_at=NOW,
        ledger_max_updated_at=NOW,
    )

    latest = repository.latest()
    assert saved_id == 1
    assert latest == snapshot


def test_multiple_saves_latest_returns_last_snapshot(
    repository: AccountSnapshotRepository,
) -> None:
    first = valued_snapshot()
    second = valued_snapshot(created_at=LATER, total_assets=61000)

    first_id = repository.save(
        first,
        cash_account_updated_at=NOW,
        ledger_max_updated_at=NOW,
    )
    second_id = repository.save(
        second,
        cash_account_updated_at=LATER,
        ledger_max_updated_at=LATER,
    )

    latest = repository.latest()
    assert first_id == 1
    assert second_id == 2
    assert latest == second


@pytest.mark.parametrize(
    "snapshot",
    [
        market_data_unavailable_snapshot(),
        cash_not_initialized_snapshot(),
    ],
)
def test_save_preserves_nullable_summary_field_snapshots(
    repository: AccountSnapshotRepository,
    snapshot: AccountSnapshot,
) -> None:
    repository.save(
        snapshot,
        cash_account_updated_at=None,
        ledger_max_updated_at=None,
    )

    assert repository.latest() == snapshot


def test_save_persists_metadata_columns_as_iso_strings_and_allows_none(
    repository: AccountSnapshotRepository,
) -> None:
    repository.save(
        valued_snapshot(),
        cash_account_updated_at=NOW,
        ledger_max_updated_at=None,
    )

    row = persisted_rows(repository)[0]
    assert row["cash_account_updated_at"] == NOW.isoformat()
    assert row["ledger_max_updated_at"] is None


def test_payload_json_is_internal_account_snapshot_json_without_raw_akshare_fields(
    repository: AccountSnapshotRepository,
) -> None:
    snapshot = valued_snapshot()

    repository.save(
        snapshot,
        cash_account_updated_at=NOW,
        ledger_max_updated_at=NOW,
    )

    payload_json = persisted_rows(repository)[0]["payload_json"]
    assert AccountSnapshot.model_validate_json(payload_json) == snapshot
    for raw_field_name in ("最新价", "涨跌幅", "代码"):
        assert raw_field_name not in payload_json
