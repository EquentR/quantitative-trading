from __future__ import annotations

from datetime import datetime

from quantitative_trading.account.models import AccountSnapshot, AccountSnapshotStatus
from quantitative_trading.decision.models import DecisionSymbolInput
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.recommendation.service import build_recommendation
from quantitative_trading.risk.models import RiskConfig, RiskContext, RiskDecision
from quantitative_trading.risk.service import apply_risk, calculate_buy_constraint
from quantitative_trading.strategy.models import StrategyAction, StrategySignal
from quantitative_trading.strategy.service import holding_risk_signals, planned_entry_signal
from quantitative_trading.instrument.models import InstrumentType, SettlementCycle


def decide_symbol(
    decision_input: DecisionSymbolInput,
    *,
    account_snapshot: AccountSnapshot | None,
    risk_config: RiskConfig,
    risk_context: RiskContext,
    recommendation_id: str,
    created_at: datetime,
) -> Recommendation:
    signal = _strategy_signal(decision_input)
    effective_risk_context = risk_context.model_copy(
        update={"instrument": decision_input.instrument or risk_context.instrument}
    )
    risk_decision = apply_risk(
        signal,
        account_snapshot,
        decision_input.position_context if decision_input.is_holding else None,
        risk_config,
        context=effective_risk_context,
    )
    if decision_input.is_holding and (
        account_snapshot is None
        or account_snapshot.status is not AccountSnapshotStatus.OK
    ):
        warning = (
            "账户估值不可用，持仓仓位信息不完整，需人工复核"
            if account_snapshot is None
            else (
                f"账户估值状态为 {account_snapshot.status.value}，"
                "持仓仓位信息不完整，需人工复核"
            )
        )
        risk_decision = risk_decision.model_copy(
            update={"reasons": list(dict.fromkeys([*risk_decision.reasons, warning]))}
        )

    position_constraint: dict[str, object] = {}
    if risk_decision.action in {StrategyAction.BUY, StrategyAction.ADD}:
        position_constraint = _position_constraint(
            decision_input,
            account_snapshot=account_snapshot,
            risk_config=risk_config,
            risk_context=risk_context,
        )
        if position_constraint.get("suggested_quantity") == 0:
            risk_decision = RiskDecision(
                allowed=False,
                original_action=risk_decision.original_action,
                action=StrategyAction.WATCH,
                reasons=[*risk_decision.reasons, "可用约束不足以买入一手，降级为观察"],
            )

    references = {name: dict(value) for name, value in decision_input.data_references.items()}
    if decision_input.plan_id is None:
        references["plan"] = {"status": "missing"}

    return build_recommendation(
        signal,
        risk_decision,
        recommendation_id=recommendation_id,
        name=decision_input.name,
        instrument=decision_input.instrument,
        position_context=decision_input.position_context,
        account_context=decision_input.account_context,
        price_context=decision_input.price_context,
        valid_until=decision_input.valid_until,
        data_time=decision_input.data_time,
        fetched_at=decision_input.fetched_at,
        created_at=created_at,
        run_id=decision_input.run_id,
        market_input_snapshot_id=decision_input.market_input_snapshot_id,
        plan_id=decision_input.plan_id,
        data_references=references,
        data_quality={
            "overall": decision_input.data_quality,
            "warnings": decision_input.warnings,
            "data_time_source": decision_input.data_time_source,
            "quote_status": decision_input.quote_status,
            "quote_usable": decision_input.quote_usable,
            "history_status": decision_input.history_status,
            "history_usable": decision_input.history_usable,
            "intraday_status": decision_input.intraday_status,
            "intraday_usable": decision_input.intraday_usable,
            "plan_status": decision_input.plan_status,
        },
        position_constraint=position_constraint,
    )


def _strategy_signal(decision_input: DecisionSymbolInput) -> StrategySignal:
    if decision_input.instrument is not None and (
        decision_input.instrument.instrument_type is InstrumentType.UNKNOWN
        or decision_input.instrument.settlement_cycle is SettlementCycle.UNKNOWN
    ):
        return _conservative_signal(
            decision_input,
            action=StrategyAction.HOLD if decision_input.is_holding else StrategyAction.WATCH,
            machine_reason="instrument_metadata_unknown",
            human_reason="证券类型或交易制度未经验证，当前只允许人工复核",
        )

    if decision_input.trading_status == "suspended":
        return _conservative_signal(
            decision_input,
            action=StrategyAction.HOLD if decision_input.is_holding else StrategyAction.AVOID,
            machine_reason="trading_suspended",
            human_reason="标的停牌或明确不可交易",
        )

    if not decision_input.quote_usable:
        return _conservative_signal(
            decision_input,
            action=(
                StrategyAction.HOLD
                if decision_input.is_holding
                else StrategyAction.AVOID
            ),
            machine_reason="quote_unavailable",
            human_reason="当前行情不可用，暂停价格触发型动作",
        )

    if not decision_input.history_usable:
        return _conservative_signal(
            decision_input,
            action=(
                StrategyAction.HOLD
                if decision_input.is_holding
                else StrategyAction.WATCH
            ),
            machine_reason="history_unavailable",
            human_reason="日线历史不可用，暂停依赖日线结构的动作",
        )

    if decision_input.is_holding:
        risk_signals = holding_risk_signals(
            symbol=decision_input.symbol,
            current_price=decision_input.current_price,
            support_price=decision_input.support_price,
            stop_loss_price=decision_input.stop_loss_price,
        )
        if risk_signals:
            signal = risk_signals[0]
            if decision_input.limit_status in {"up", "down"}:
                signal = signal.model_copy(
                    update={
                        "machine_reason": [
                            *signal.machine_reason,
                            "price_limit_execution_uncertain",
                        ],
                        "human_reason": [
                            *signal.human_reason,
                            "标的处于涨跌停状态，卖出或减仓建议可能无法成交",
                        ],
                    }
                )
            return signal

        if (
            decision_input.trading_status == "unknown"
            or decision_input.limit_status == "unknown"
        ):
            return _conservative_signal(
                decision_input,
                action=StrategyAction.HOLD,
                machine_reason="tradeability_unknown",
                human_reason="无法确认交易状态或涨跌停状态，暂停新增买入或加仓",
            )

        if decision_input.limit_status in {"up", "down"}:
            return _conservative_signal(
                decision_input,
                action=StrategyAction.HOLD,
                machine_reason="price_limit_blocks_entry",
                human_reason="标的处于涨跌停状态，禁止新增买入或加仓",
            )

        entry_context_ready = (
            decision_input.plan_active
            and decision_input.plan_allows_entry
            and decision_input.plan_condition_met
            and decision_input.daily_structure_confirmed
        )
        if entry_context_ready and not decision_input.intraday_usable:
            return _conservative_signal(
                decision_input,
                action=StrategyAction.HOLD,
                machine_reason="intraday_data_unusable",
                human_reason="分时数据仅可展示，不能作为加仓确认",
            )

        if (
            entry_context_ready
            and decision_input.intraday_usable
            and decision_input.intraday_strength == "strong"
            and decision_input.trading_status == "normal"
            and decision_input.limit_status == "none"
        ):
            return planned_entry_signal(
                symbol=decision_input.symbol,
                has_position=True,
                plan_active=True,
                plan_allows_entry=True,
                plan_condition_met=True,
                daily_structure_confirmed=True,
                intraday_strength=(
                    decision_input.intraday_strength
                    if decision_input.intraday_usable
                    else "neutral"
                ),
                money_flow_confirmed=decision_input.money_flow_confirmed,
                money_flow_applicable=not (
                    decision_input.instrument is not None
                    and decision_input.instrument.instrument_type is InstrumentType.ETF
                ),
                data_quality=decision_input.data_quality,
                invalid_if=decision_input.invalid_if,
                quote_usable=decision_input.quote_usable,
                history_usable=decision_input.history_usable,
                intraday_usable=decision_input.intraday_usable,
            )

        return _conservative_signal(
            decision_input,
            action=StrategyAction.HOLD,
            machine_reason="holding_conditions_intact",
            human_reason="持仓未触发减仓或退出条件",
        )

    if decision_input.limit_status in {"up", "down"}:
        return _conservative_signal(
            decision_input,
            action=StrategyAction.WATCH,
            machine_reason="price_limit_blocks_entry",
            human_reason="标的处于涨跌停状态，禁止新增买入",
        )

    if (
        decision_input.trading_status == "unknown"
        or decision_input.limit_status == "unknown"
    ):
        return _conservative_signal(
            decision_input,
            action=StrategyAction.WATCH,
            machine_reason="tradeability_unknown",
            human_reason="无法确认交易状态或涨跌停状态，暂停新增买入",
        )

    return planned_entry_signal(
        symbol=decision_input.symbol,
        has_position=False,
        plan_active=decision_input.plan_active,
        plan_allows_entry=decision_input.plan_allows_entry,
        plan_condition_met=decision_input.plan_condition_met,
        daily_structure_confirmed=decision_input.daily_structure_confirmed,
        intraday_strength=(
            decision_input.intraday_strength
            if decision_input.intraday_usable
            else "neutral"
        ),
        money_flow_confirmed=decision_input.money_flow_confirmed,
        money_flow_applicable=not (
            decision_input.instrument is not None
            and decision_input.instrument.instrument_type is InstrumentType.ETF
        ),
        data_quality=decision_input.data_quality,
        invalid_if=decision_input.invalid_if,
        quote_usable=decision_input.quote_usable,
        history_usable=decision_input.history_usable,
        intraday_usable=decision_input.intraday_usable,
    )


def _conservative_signal(
    decision_input: DecisionSymbolInput,
    *,
    action: StrategyAction,
    machine_reason: str,
    human_reason: str,
) -> StrategySignal:
    return StrategySignal(
        symbol=decision_input.symbol,
        action=action,
        confidence="low",
        machine_reason=[machine_reason],
        human_reason=[human_reason],
        invalid_if=decision_input.invalid_if,
    )


def _position_constraint(
    decision_input: DecisionSymbolInput,
    *,
    account_snapshot: AccountSnapshot | None,
    risk_config: RiskConfig,
    risk_context: RiskContext,
) -> dict[str, object]:
    if (
        account_snapshot is None
        or account_snapshot.total_assets is None
        or account_snapshot.available_buying_cash is None
        or account_snapshot.market_value is None
        or decision_input.current_price is None
    ):
        return {}

    current_position_value = decision_input.position_context.get("market_value", 0)
    if not isinstance(current_position_value, int | float):
        current_position_value = 0

    return calculate_buy_constraint(
        current_price=decision_input.current_price,
        total_assets=account_snapshot.total_assets,
        available_cash=account_snapshot.available_buying_cash,
        current_position_value=float(current_position_value),
        current_total_position_value=account_snapshot.market_value,
        current_daily_new_buy_value=risk_context.daily_new_buy_value,
        has_position=decision_input.is_holding,
        config=risk_config,
        requested_value=risk_context.proposed_value,
    ).model_dump(mode="json")
