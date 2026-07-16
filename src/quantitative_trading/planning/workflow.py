from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from quantitative_trading.planning.models import (
    MarketPlanSymbolInput,
    PlanCondition,
    PlanSymbolContext,
    TradingPlan,
    TradingPlanStatus,
)
from quantitative_trading.planning.service import plan_valid_until
from quantitative_trading.instrument.models import InstrumentType, SettlementCycle


def build_market_trading_plan(
    *,
    trading_day: date,
    now: datetime,
    timezone: str,
    universe_snapshot_id: int,
    account_snapshot_id: int | None,
    ledger_max_updated_at: datetime | None,
    source_run_id: int | str,
    market_input_snapshot_id: int,
    data_time: datetime,
    version: int,
    symbols: list[MarketPlanSymbolInput],
) -> TradingPlan:
    key_levels: dict[str, dict[str, float]] = {}
    candidate_actions: dict[str, list[str]] = {}
    invalid_if: dict[str, list[str]] = {}
    symbol_contexts: dict[str, PlanSymbolContext] = {}
    warnings: list[str] = []
    holding_symbols: list[str] = []
    watch_symbols: list[str] = []

    for item in symbols:
        levels, level_warnings = _market_structure_levels(item)
        key_levels[item.symbol] = levels
        warnings.extend(level_warnings)
        warnings.extend(item.warnings)

        allowed_actions = (
            ["sell", "reduce", "hold", "add"]
            if item.is_holding
            else ["watch", "avoid", "buy"]
        )
        instrument_unknown = item.instrument is not None and (
            item.instrument.instrument_type is InstrumentType.UNKNOWN
            or item.instrument.settlement_cycle is SettlementCycle.UNKNOWN
        )
        if instrument_unknown:
            allowed_actions = ["hold"] if item.is_holding else ["watch"]
        elif item.data_quality in {"failed", "stale"}:
            allowed_actions = (
                ["hold", "reduce", "sell"] if item.is_holding else ["watch", "avoid"]
            )

        candidate_actions[item.symbol] = list(allowed_actions)
        symbol_invalid_if = ["计划适用交易日收盘后失效"]
        support = levels.get("support")
        if support is not None:
            symbol_invalid_if.insert(0, f"跌破计划支撑位 {support:.3f}")
        invalid_if[item.symbol] = symbol_invalid_if

        context = PlanSymbolContext(
            symbol=item.symbol,
            name=item.name,
            instrument=item.instrument,
            sources=item.sources,
            is_holding=item.is_holding,
            trend=dict(item.daily_features),
            daily_feature_facts={
                name: dict(fact) for name, fact in item.daily_feature_facts.items()
            },
            volume_price={
                "volume_ratio": item.daily_features.get("volume_ratio"),
                "current_price": item.current_price,
                "market_structure": dict(item.market_structure),
            },
            money_flow=dict(item.money_flow),
            conditions=_entry_conditions(item, levels),
            allowed_actions=allowed_actions,
            prohibited_actions=_prohibited_actions(allowed_actions),
            position_constraint={
                "max_position_ratio": 0.30,
                "max_total_position_ratio": 0.80,
                "max_daily_new_buy_ratio": 0.20,
            },
            position_context=dict(item.position_context),
            account_context=dict(item.account_context),
            risks=["市场数据可能延迟", "盘中风险信号可以覆盖收盘计划"],
            invalid_if=symbol_invalid_if,
            data_quality=item.data_quality,
            warnings=item.warnings,
        )
        symbol_contexts[item.symbol] = context
        if item.is_holding:
            holding_symbols.append(item.symbol)
        else:
            watch_symbols.append(item.symbol)

    plan_quality = (
        "complete"
        if symbols and all(item.data_quality == "complete" for item in symbols)
        else "degraded"
    )
    return TradingPlan(
        plan_id=f"plan-{trading_day:%Y%m%d}-v{version}",
        trading_day=trading_day,
        generated_at=now.astimezone(UTC),
        valid_until=plan_valid_until(
            datetime.combine(trading_day, time(12, 0), tzinfo=ZoneInfo(timezone)),
            timezone=timezone,
        ),
        universe_snapshot_id=universe_snapshot_id,
        account_snapshot_id=account_snapshot_id,
        ledger_max_updated_at=ledger_max_updated_at,
        watch_symbols=watch_symbols,
        holding_symbols=holding_symbols,
        key_levels=key_levels,
        candidate_actions=candidate_actions,
        invalid_if=invalid_if,
        warnings=_stable_unique(warnings),
        status=TradingPlanStatus.ACTIVE,
        version=version,
        source_run_id=source_run_id,
        market_input_snapshot_id=market_input_snapshot_id,
        data_time=data_time,
        data_quality=plan_quality,
        symbol_contexts=symbol_contexts,
    )


def _market_structure_levels(
    item: MarketPlanSymbolInput,
) -> tuple[dict[str, float], list[str]]:
    support = item.market_structure.get("support")
    resistance = item.market_structure.get("resistance")
    atr14 = item.market_structure.get("atr14")
    if not isinstance(support, int | float) and not isinstance(resistance, int | float):
        return {}, [f"{item.symbol} 市场结构价位不可用，未使用持仓成本回退"]

    levels: dict[str, float] = {}
    if isinstance(support, int | float):
        levels["support"] = float(support)
    if isinstance(resistance, int | float):
        levels["resistance"] = float(resistance)
    if isinstance(support, int | float) and isinstance(atr14, int | float):
        levels["stop_loss"] = round(float(support) - 0.5 * float(atr14), 3)
    return levels, []


def _entry_conditions(
    item: MarketPlanSymbolInput,
    levels: dict[str, float],
) -> list[PlanCondition]:
    conditions: list[PlanCondition] = []
    resistance = levels.get("resistance")
    if resistance is not None:
        conditions.append(
            PlanCondition(
                condition_id="price-structure-breakout",
                metric="current_price",
                operator="gte",
                threshold=resistance,
                required=True,
                rationale="价格达到计划市场结构压力位",
            )
        )
    conditions.append(
        PlanCondition(
            condition_id="intraday-strength",
            metric="intraday_strength",
            operator="eq",
            threshold="strong",
            required=True,
            rationale="分时强弱达到 strong",
        )
    )
    if item.money_flow.get("status") != "not_applicable":
        conditions.append(
            PlanCondition(
                condition_id="money-flow-confirmation",
                metric="money_flow_positive",
                operator="eq",
                threshold=1,
                required=False,
                rationale="资金流只提供额外确认或过滤",
            )
        )
    return conditions


def _prohibited_actions(allowed_actions: list[str]) -> list[str]:
    all_actions = ["buy", "sell", "add", "reduce", "hold", "watch", "avoid"]
    return [action for action in all_actions if action not in allowed_actions]


def _stable_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))
