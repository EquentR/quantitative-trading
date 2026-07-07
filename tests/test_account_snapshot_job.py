from datetime import UTC, date, datetime

from quantitative_trading.cash.models import CashAccount
from quantitative_trading.config import Settings
from quantitative_trading.ledger.models import Position
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus
from quantitative_trading.runtime import account_snapshot_job
from quantitative_trading.runtime.account_snapshot_job import (
    create_and_save_account_snapshot_with_connection,
)
from quantitative_trading.storage.sqlite import connect, migrate


CAPTURED_AT = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)
CHANGED_AT = datetime(2026, 7, 7, 3, 0, tzinfo=UTC)


class FakeMarketProvider:
    def get_quotes(self, symbols):
        return {
            "600000": QuoteSnapshot(
                symbol="600000",
                name="浦发银行",
                current_price=10.5,
                change_pct=1.2,
                data_time=CAPTURED_AT,
                fetched_at=CAPTURED_AT,
                source="fake",
                status=QuoteStatus.OK,
            )
        }


class ChangingCashService:
    def __init__(self, repository) -> None:
        self.calls = 0

    def get_account(self) -> CashAccount:
        self.calls += 1
        updated_at = CAPTURED_AT if self.calls == 1 else CHANGED_AT
        return CashAccount(
            cash_balance=50000,
            total_transfer_in=50000,
            total_transfer_out=0,
            updated_at=updated_at,
        )


class ChangingLedgerService:
    def __init__(self, repository) -> None:
        self.calls = 0

    def list_positions(self) -> list[Position]:
        self.calls += 1
        updated_at = CAPTURED_AT if self.calls == 1 else CHANGED_AT
        return [
            Position(
                symbol="600000",
                name="浦发银行",
                quantity=1000,
                available_quantity=800,
                cost_price=9.5,
                opened_at=date(2026, 7, 6),
                updated_at=updated_at,
            )
        ]


def test_snapshot_job_metadata_uses_same_captured_inputs_as_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(account_snapshot_job, "ReadOnlyCashService", ChangingCashService)
    monkeypatch.setattr(account_snapshot_job, "ReadOnlyLedgerService", ChangingLedgerService)
    settings = Settings(database_path=tmp_path / "snapshot.db")
    with connect(settings) as connection:
        migrate(connection)

        created = create_and_save_account_snapshot_with_connection(
            connection,
            market=FakeMarketProvider(),
        )

        row = connection.execute(
            """
            SELECT cash_account_updated_at, ledger_max_updated_at
            FROM account_snapshots
            WHERE id = ?
            """,
            (created.snapshot_id,),
        ).fetchone()

    assert created.snapshot.cash_balance == 50000
    assert created.snapshot.positions[0].ledger_updated_at == CAPTURED_AT
    assert row["cash_account_updated_at"] == CAPTURED_AT.isoformat()
    assert row["ledger_max_updated_at"] == CAPTURED_AT.isoformat()
