from datetime import UTC, date, datetime, timedelta

from quantitative_trading.config import Settings
from quantitative_trading.recommendation.identity import with_recommendation_identity
from quantitative_trading.recommendation.models import Recommendation, RecommendationAction
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.notification.models import NotificationStatus, NotificationSummary
from quantitative_trading.notification.repository import NotificationRepository


NOW = datetime(2026, 7, 13, 2, 30, tzinfo=UTC)


def metadata(rule_version: str) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol="600000",
        name="测试股票",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.A_SHARE,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="exchange_catalog",
        metadata_checked_at=NOW,
        rule_version=rule_version,
    )


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


def test_linked_current_view_selects_latest_per_symbol_in_sql(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "recommendation-current-view.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = RecommendationRepository(connection)
        history_newer_market_time = recommendation(
            "rec-600-old",
            "600000",
            RecommendationAction.HOLD,
            data_time=NOW + timedelta(minutes=10),
        )
        current_600 = recommendation(
            "rec-600-current",
            "600000",
            RecommendationAction.REDUCE,
            data_time=NOW,
        )
        current_000001 = recommendation(
            "rec-000001-current",
            "000001",
            RecommendationAction.WATCH,
            data_time=NOW + timedelta(minutes=2),
        )
        repository.save_many(
            [history_newer_market_time],
            created_at=NOW + timedelta(minutes=2, seconds=30),
        )
        repository.save_many(
            [current_000001],
            created_at=NOW + timedelta(minutes=2),
        )
        repository.save_many(
            [current_600],
            created_at=NOW + timedelta(minutes=3),
        )
        notification_repository = NotificationRepository(connection)
        notification = NotificationSummary(
            notification_id="notif-600-current",
            recommendation_id=current_600.recommendation_id,
            symbol=current_600.symbol,
            action=current_600.action.value,
            confidence=current_600.confidence,
            key_price=10.0,
            reason=list(current_600.reason),
            risk=list(current_600.risk["invalid_if"]),
            data_time=current_600.data_time,
            audit_id="audit-600-current",
            status=NotificationStatus.READ,
            created_at=NOW + timedelta(minutes=3),
        )
        notification_repository.save(notification)
        notification_repository.save_canonical_group(
            "canonical-600-current",
            notification.notification_id,
            created_at=NOW + timedelta(minutes=3),
        )
        notification_repository.link_recommendation(
            current_600.recommendation_id,
            notification.notification_id,
            "canonical-600-current",
            created_at=NOW + timedelta(minutes=3),
        )

        current = repository.list_linked(view="current", limit=20, offset=0)
        second_current_page = repository.list_linked(
            view="current",
            limit=1,
            offset=1,
        )
        history = repository.list_linked(view="history", limit=20, offset=0)
        current_count = repository.count_current()
        history_count = repository.count()

    assert [item.recommendation.recommendation_id for item in current] == [
        "rec-600-current",
        "rec-000001-current",
    ]
    assert current_count == 2
    assert [item.recommendation.recommendation_id for item in second_current_page] == [
        "rec-000001-current"
    ]
    assert current[0].notification is not None
    assert current[0].notification.model_dump(mode="json") == {
        "notification_id": "notif-600-current",
        "status": "read",
    }
    assert current[1].notification is None
    assert history_count == 3
    assert [item.recommendation.recommendation_id for item in history] == [
        "rec-600-old",
        "rec-000001-current",
        "rec-600-current",
    ]
    assert history[2].notification == current[0].notification


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


def test_same_cycle_reuses_identical_recommendation_but_keeps_material_change(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "recommendation-identity.db")
    period_start = datetime(2026, 7, 13, 2, 30, tzinfo=UTC)
    original = recommendation(
        "temporary-id",
        "600000",
        RecommendationAction.WATCH,
        data_time=NOW,
    ).model_copy(
        update={
            "condition_context": {
                "plan_conditions": ["price_above_support"],
                "evaluation": ["matched"],
            }
        }
    )
    identical = original.model_copy(update={"recommendation_id": "another-temporary-id"})
    changed = original.model_copy(
        update={
            "recommendation_id": "changed-temporary-id",
            "risk": {
                **original.risk,
                "invalid_if": ["跌破更新后的计划支撑位"],
            },
        }
    )

    original = with_recommendation_identity(
        original,
        trade_date=date(2026, 7, 13),
        period_start=period_start,
        plan_version=2,
    )
    identical = with_recommendation_identity(
        identical,
        trade_date=date(2026, 7, 13),
        period_start=period_start,
        plan_version=2,
    )
    changed = with_recommendation_identity(
        changed,
        trade_date=date(2026, 7, 13),
        period_start=period_start,
        plan_version=2,
    )

    with connect(settings) as connection:
        migrate(connection)
        repository = RecommendationRepository(connection)
        saved = repository.save_many(
            [original, identical, changed],
            created_at=NOW,
        )

        assert repository.count(symbol="600000") == 2
        assert original.recommendation_id == identical.recommendation_id
        assert changed.recommendation_id != original.recommendation_id
        assert changed.condition_fingerprint != original.condition_fingerprint
        assert [item.recommendation_id for item in saved] == [
            original.recommendation_id,
            original.recommendation_id,
            changed.recommendation_id,
        ]


def test_recommendation_identity_aligns_to_three_minute_cycle() -> None:
    identified = with_recommendation_identity(
        recommendation(
            "temporary-id",
            "600000",
            RecommendationAction.WATCH,
            data_time=NOW,
        ),
        trade_date=date(2026, 7, 13),
        period_start=datetime(2026, 7, 13, 2, 32, 59, tzinfo=UTC),
        plan_version=1,
    )

    assert identified.decision_cycle == "2026-07-13T02:30:00+00:00"
    assert identified.decision_trade_date == date(2026, 7, 13)


def test_recommendation_fingerprint_changes_with_instrument_rule_version() -> None:
    first = recommendation(
        "temporary-id-1", "600000", RecommendationAction.WATCH, data_time=NOW
    ).model_copy(update={"instrument": metadata("instrument-rules-v1")})
    second = first.model_copy(update={"instrument": metadata("instrument-rules-v2")})

    first = with_recommendation_identity(
        first,
        trade_date=date(2026, 7, 13),
        period_start=NOW,
        plan_version=1,
    )
    second = with_recommendation_identity(
        second,
        trade_date=date(2026, 7, 13),
        period_start=NOW,
        plan_version=1,
    )

    assert first.condition_fingerprint != second.condition_fingerprint


def test_recommendation_fingerprint_v2_ignores_volatile_instrument_time() -> None:
    instrument = metadata("instrument-rules-v2")
    first = recommendation(
        "temporary-id-1", "600000", RecommendationAction.WATCH, data_time=NOW
    ).model_copy(update={"instrument": instrument})
    second = first.model_copy(
        update={
            "instrument": instrument.model_copy(
                update={"metadata_checked_at": NOW + timedelta(days=1)}
            ),
            "data_time": NOW + timedelta(minutes=3),
            "created_at": NOW + timedelta(minutes=3),
        }
    )

    first = with_recommendation_identity(
        first,
        trade_date=date(2026, 7, 13),
        period_start=NOW,
        plan_version=1,
    )
    second = with_recommendation_identity(
        second,
        trade_date=date(2026, 7, 13),
        period_start=NOW,
        plan_version=1,
    )

    assert first.condition_fingerprint == second.condition_fingerprint
    assert first.condition_fingerprint_version == 2
    assert second.condition_fingerprint_version == 2
