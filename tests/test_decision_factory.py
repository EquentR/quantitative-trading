from datetime import UTC, datetime

from quantitative_trading.config import Settings
from quantitative_trading.decision.factory import build_decision_workflow
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.market.adapters import (
    AkShareEtfDailyBarProvider,
    AkShareEtfIntradayProvider,
)
from quantitative_trading.market.providers import AkShareEtfMarketProvider
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import InstrumentRepository


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


def test_decision_factory_wires_etf_market_providers(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "factory-etf.db", enable_market_fetch=True)
    with connect(settings) as connection:
        migrate(connection)
        workflow = build_decision_workflow(
            connection,
            settings,
            now=lambda: datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        )

    assert isinstance(workflow.etf_quote_provider, AkShareEtfMarketProvider)
    assert isinstance(workflow.etf_daily_provider, AkShareEtfDailyBarProvider)
    assert isinstance(workflow.etf_intraday_provider, AkShareEtfIntradayProvider)


def test_decision_factory_loads_persisted_instrument_metadata(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "factory-instrument.db", enable_market_fetch=False)
    metadata = InstrumentMetadata(
        symbol="510300",
        name="沪深300ETF",
        exchange=Exchange.SH,
        instrument_type=InstrumentType.ETF,
        settlement_cycle=SettlementCycle.T1,
        price_limit_ratio=0.10,
        metadata_source="exchange_catalog",
        metadata_checked_at=datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        rule_version="instrument-rules-v1",
    )
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog([metadata])
        workflow = build_decision_workflow(
            connection,
            settings,
            now=lambda: datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        )

        loaded = workflow._load_instrument_metadata(["510300"])

    assert loaded == {"510300": metadata}
