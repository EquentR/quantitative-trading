from datetime import UTC, datetime

from quantitative_trading.config import Settings
from quantitative_trading.decision.factory import build_decision_workflow
from quantitative_trading.storage.sqlite import connect, migrate


def test_decision_factory_injects_configured_stale_threshold(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "factory.db",
        enable_market_fetch=False,
        market_stale_trading_minutes=11,
    )
    with connect(settings) as connection:
        migrate(connection)
        workflow = build_decision_workflow(
            connection,
            settings,
            now=lambda: datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        )

    assert workflow.stale_trading_minutes == 11


def test_decision_factory_injects_versioned_intraday_strength_rules(tmp_path) -> None:
    settings = Settings(
        database_path=tmp_path / "factory-rules.db",
        enable_market_fetch=False,
        market_strength_rule_version="intraday-strength-custom-v2",
        market_strength_previous_close_pct=0.8,
        market_strength_volume_high=1.8,
    )
    with connect(settings) as connection:
        migrate(connection)
        workflow = build_decision_workflow(
            connection,
            settings,
            now=lambda: datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        )

    assert workflow.strength_rules.rule_version == "intraday-strength-custom-v2"
    assert workflow.strength_rules.previous_close_pct == 0.8
    assert workflow.strength_rules.volume_high == 1.8
