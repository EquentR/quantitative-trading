from datetime import UTC, datetime

import pytest

from quantitative_trading.cash.repository import CashAccountRepository
from quantitative_trading.cash.service import CashService
from quantitative_trading.config import Settings
from quantitative_trading.feedback.models import ExecutionFeedbackInput
from quantitative_trading.feedback.repository import FeedbackRepository
from quantitative_trading.feedback.service import FeedbackService
from quantitative_trading.ledger.models import PositionInput
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.ledger.service import LedgerService
from quantitative_trading.storage.sqlite import connect
from quantitative_trading.storage.sqlite import migrate


NOW = datetime(2026, 7, 9, 10, 30, tzinfo=UTC)


def position_input() -> PositionInput:
    return PositionInput(
        symbol="600000",
        name="浦发银行",
        quantity=1000,
        available_quantity=800,
        cost_price=9.5,
        opened_at=NOW.date(),
        note="first lot",
    )


def test_record_feedback_does_not_mutate_position_or_cash_ledgers(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "feedback.db")
    with connect(settings) as connection:
        migrate(connection)
        position_repository = PositionRepository(connection)
        cash_repository = CashAccountRepository(connection)
        LedgerService(position_repository).add_position(position_input(), now=NOW)
        CashService(cash_repository).initialize(50000, now=NOW, note="initial principal")
        original_position = position_repository.get("600000")
        original_cash_account = cash_repository.get()
        original_transactions = cash_repository.list_transactions(limit=20)

        service = FeedbackService(
            FeedbackRepository(connection),
            id_factory=lambda: "feedback-1",
        )
        feedback = service.record(
            ExecutionFeedbackInput(
                recommendation_id="rec-1",
                executed=True,
                execution_price=10.25,
                execution_quantity=100,
                note="manual execution",
            ),
            now=NOW,
        )

        assert feedback.feedback_id == "feedback-1"
        assert feedback.recommendation_id == "rec-1"
        assert feedback.executed is True
        assert position_repository.get("600000") == original_position
        assert cash_repository.get() == original_cash_account
        assert cash_repository.list_transactions(limit=20) == original_transactions


def test_record_feedback_sanitizes_note(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "feedback.db")
    with connect(settings) as connection:
        migrate(connection)
        service = FeedbackService(
            FeedbackRepository(connection),
            id_factory=lambda: "feedback-1",
        )

        feedback = service.record(
            ExecutionFeedbackInput(
                recommendation_id="rec-1",
                executed=False,
                note="api_key=raw-key token=raw-token cookie=raw-cookie",
            ),
            now=NOW,
        )

    text = feedback.model_dump_json().lower()
    assert "api_key" not in text
    assert "token" not in text
    assert "cookie" not in text
    assert "raw-key" not in text
    assert "raw-token" not in text
    assert "raw-cookie" not in text


def test_feedback_created_at_must_be_timezone_aware(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "feedback.db")
    with connect(settings) as connection:
        migrate(connection)
        service = FeedbackService(FeedbackRepository(connection))

        with pytest.raises(ValueError, match="created_at must be timezone-aware"):
            service.record(
                ExecutionFeedbackInput(recommendation_id="rec-1", executed=False),
                now=datetime(2026, 7, 9, 10, 30),
            )
