from datetime import UTC, datetime, timedelta

from quantitative_trading.config import Settings
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 13, 2, 30, tzinfo=UTC)


def recommendation(
    recommendation_id: str,
    symbol: str,
    action: RecommendationAction,
    *,
    data_time: datetime,
) -> Recommendation:
    return Recommendation(
        recommendation_id=recommendation_id,
        symbol=symbol,
        name="测试股票",
        action=action,
        confidence="medium",
        position_context={"source": "manual_ledger"},
        account_context={"source": "manual_cash_account"},
        price_context={"current_price": 10},
        reason=["测试规则命中"],
        risk={"invalid_if": ["测试条件失效"], "notes": []},
        valid_until=NOW + timedelta(hours=5),
        data_time=data_time,
        created_at=data_time,
    )


def test_latest_for_symbol_and_filtered_pagination(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "recommendations.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = RecommendationRepository(connection)
        repository.save_many(
            [
                recommendation("rec-1", "600000", RecommendationAction.WATCH, data_time=NOW),
                recommendation(
                    "rec-2",
                    "000001",
                    RecommendationAction.BUY,
                    data_time=NOW + timedelta(minutes=3),
                ),
                recommendation(
                    "rec-3",
                    "600000",
                    RecommendationAction.BUY,
                    data_time=NOW + timedelta(minutes=6),
                ),
            ],
            created_at=NOW,
        )

        latest = repository.latest_for_symbol("600000")
        filtered = repository.list(
            symbol="600000",
            action=RecommendationAction.BUY,
            limit=1,
            offset=0,
        )
        count = repository.count(symbol="600000")

    assert latest is not None
    assert latest.recommendation_id == "rec-3"
    assert [item.recommendation_id for item in filtered] == ["rec-3"]
    assert count == 2


def test_save_many_can_participate_in_caller_transaction(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "recommendations.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = RecommendationRepository(connection)
        repository.save_many(
            [recommendation("rec-rollback", "600000", RecommendationAction.HOLD, data_time=NOW)],
            created_at=NOW,
            commit=False,
        )
        connection.rollback()

        assert repository.get("rec-rollback") is None
