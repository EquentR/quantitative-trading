from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path as ApiPath, Query
from pydantic import BaseModel, Field

from quantitative_trading.api.dependencies import (
    ApiContainer,
    connection_scope,
    get_container,
    require_token as require_auth,
)
from quantitative_trading.api.errors import ApiError
from quantitative_trading.ledger.models import Position
from quantitative_trading.ledger.repository import PositionRepository
from quantitative_trading.instrument.models import InstrumentMetadata
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.market.features import (
    calculate_daily_features,
    select_market_structure,
)
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    ComponentStatus,
    DailyBar,
    DatasetQuality,
    HistorySnapshot,
    IntradayStrengthSnapshot,
    MarketCaptureResult,
    MarketCaptureRun,
    MarketInputSnapshot,
    MoneyFlowSnapshot,
    QuoteSnapshot,
    QuoteStatus,
)
from quantitative_trading.market.repositories import (
    DailyBarRepository,
    HistorySnapshotRepository,
    IntradayStrengthSnapshotRepository,
    MarketCaptureResultRepository,
    MarketCaptureRunRepository,
    MinuteBarRepository,
    MoneyFlowRepository,
    MoneyFlowSnapshotRepository,
)
from quantitative_trading.market.repository import (
    MarketInputSnapshotRepository,
    QuoteSnapshotRepository,
)
from quantitative_trading.planning.models import TradingPlan
from quantitative_trading.recommendation.models import Recommendation
from quantitative_trading.recommendation.repository import RecommendationRepository
from quantitative_trading.universe.models import UniverseMember
from quantitative_trading.universe.repository import UniverseSnapshotRepository
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.repository import WatchPinnedRepository


router = APIRouter(
    prefix="/market",
    tags=["market"],
    dependencies=[Depends(require_auth)],
)

SQLITE_SIGNED_64_BIT_INTEGER_MAX = 9_223_372_036_854_775_807
SHANGHAI = timezone(timedelta(hours=8))
QualityStatus = Literal[
    "complete",
    "ok",
    "partial",
    "degraded",
    "stale",
    "failed",
    "unavailable",
    "not_applicable",
]
StrengthLabel = Literal["strong", "neutral", "weak", "unavailable"]
RecommendationActionValue = Literal[
    "buy", "sell", "add", "reduce", "hold", "watch", "avoid"
]
SymbolPath = Annotated[str, ApiPath(pattern=r"^[0-9]{6}$")]
SymbolQuery = Annotated[str, Query(pattern=r"^[0-9]{6}$")]
SnapshotIdPath = Annotated[int, ApiPath(gt=0, le=SQLITE_SIGNED_64_BIT_INTEGER_MAX)]


class MarketSymbolSummary(BaseModel):
    symbol: str
    name: str
    sources: list[str]
    current_price: float | None
    change_pct: float | None
    recommendation_action: RecommendationActionValue | None
    intraday_strength: StrengthLabel
    plan_status: str | None
    quality_status: QualityStatus
    unread_count: int
    data_time: datetime | None
    warnings: list[str]


class PaginatedMarketSymbols(BaseModel):
    items: list[MarketSymbolSummary]
    total: int
    page: int
    page_size: int


class MarketStrengthComponent(BaseModel):
    key: str
    label: str
    value: float | None
    status: QualityStatus
    direction: Literal[-1, 0, 1] | None
    reason: str


class PositionOverview(BaseModel):
    quantity: int
    available_quantity: int
    cost_price: float
    floating_pnl_pct: float | None


class PlanOverview(BaseModel):
    plan_id: str
    status: str
    allowed_actions: list[str]
    invalid_if: list[str]
    valid_until: datetime


class RecommendationOverview(BaseModel):
    recommendation_id: str
    action: RecommendationActionValue
    confidence: str
    reason: list[str]
    data_time: datetime


class MarketStructureOverview(BaseModel):
    support: float | None
    resistance: float | None
    atr14: float | None
    trend: str
    reason: str


class StrengthOverview(BaseModel):
    label: StrengthLabel
    confidence: str
    components: list[MarketStrengthComponent]
    degraded_reason: str | None


class MarketOverview(BaseModel):
    symbol: str
    name: str
    snapshot_id: int | None
    status: QualityStatus
    data_time: datetime | None
    fetched_at: datetime
    warnings: list[str]
    position: PositionOverview | None
    plan: PlanOverview | None
    recommendation: RecommendationOverview | None
    market_structure: MarketStructureOverview | None
    intraday_strength: StrengthOverview | None
    risks: list[str]


class DailyBarResponse(BaseModel):
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma60: float | None


class DailyBarsResponse(BaseModel):
    symbol: str
    adjustment: Literal["forward"] = "forward"
    status: QualityStatus
    data_time: datetime | None
    fetched_at: datetime
    warnings: list[str]
    bars: list[DailyBarResponse]


class MoneyFlowRow(BaseModel):
    trade_date: date
    main_net_amount: float
    main_net_ratio: float
    super_large_net_amount: float
    super_large_net_ratio: float
    large_net_amount: float
    large_net_ratio: float
    medium_net_amount: float
    medium_net_ratio: float
    small_net_amount: float
    small_net_ratio: float


class MoneyFlowResponse(BaseModel):
    symbol: str
    status: QualityStatus
    data_time: datetime | None
    fetched_at: datetime
    warnings: list[str]
    rows: list[MoneyFlowRow]


class MinuteBarResponse(BaseModel):
    minute: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    vwap: float | None


class RecommendationMarker(BaseModel):
    time: str
    action: RecommendationActionValue
    price: float = Field(gt=0)
    recommendation_id: str


class MinuteBarsResponse(BaseModel):
    symbol: str
    trade_date: date | None
    status: QualityStatus
    data_time: datetime | None
    fetched_at: datetime
    previous_close: float | None
    warnings: list[str]
    bars: list[MinuteBarResponse]
    recommendation_markers: list[RecommendationMarker]


class IntradayStrengthResponse(BaseModel):
    symbol: str
    status: QualityStatus
    label: StrengthLabel
    confidence: str
    data_time: datetime | None
    fetched_at: datetime
    coverage_ratio: float | None
    last_minute: str | None
    degraded_reason: str | None
    rule_version: str
    components: list[MarketStrengthComponent]
    warnings: list[str]


class DatasetStatusCounts(BaseModel):
    complete: int = 0
    degraded: int = 0
    failed: int = 0
    stale: int = 0
    not_applicable: int = 0


class MarketRunSummary(MarketCaptureRun):
    dataset_counts: dict[CaptureDataset, DatasetStatusCounts]


class PaginatedMarketRuns(BaseModel):
    items: list[MarketRunSummary]
    total: int
    page: int
    page_size: int


class MarketRunDetail(MarketRunSummary):
    results: list[MarketCaptureResult]


class MarketTraceDataset(BaseModel):
    dataset: Literal["quote", "history", "money_flow", "intraday_strength"]
    reference_id: int | None
    status: QualityStatus
    source: str
    data_start: str | None
    data_end: str | None
    data_time: datetime | None
    fetched_at: datetime | None
    warnings: list[str]


class MarketSnapshotTrace(BaseModel):
    symbol: str
    instrument: InstrumentMetadata | None
    run_id: str | None
    snapshot_id: int
    plan_id: str | None
    recommendation_id: str | None
    audit_id: str | None
    data_time: datetime | None
    fetched_at: datetime
    status: QualityStatus
    warnings: list[str]
    thresholds: dict[str, float]
    datasets: list[MarketTraceDataset]


def _now() -> datetime:
    return datetime.now(UTC)


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _snapshot_not_found(snapshot_id: int) -> ApiError:
    return ApiError(
        status_code=404,
        code="market_snapshot_not_found",
        message="market snapshot not found",
        details={"snapshot_id": snapshot_id},
    )


def _run_not_found(run_id: str) -> ApiError:
    return ApiError(
        status_code=404,
        code="market_run_not_found",
        message="market capture run not found",
        details={"run_id": run_id},
    )


def _capture_dataset_counts(
    results: list[MarketCaptureResult],
) -> dict[CaptureDataset, DatasetStatusCounts]:
    counts: dict[CaptureDataset, DatasetStatusCounts] = {}
    for result in results:
        current = counts.setdefault(result.dataset, DatasetStatusCounts())
        counts[result.dataset] = current.model_copy(
            update={result.status.value: getattr(current, result.status.value) + 1}
        )
    return counts


def _plan_contains_symbol(plan: TradingPlan, symbol: str) -> bool:
    return any(
        symbol in collection
        for collection in (
            plan.holding_symbols,
            plan.watch_symbols,
            plan.key_levels,
            plan.candidate_actions,
            plan.invalid_if,
            plan.symbol_contexts,
        )
    )


def _latest_plan_for_symbol(
    connection: sqlite3.Connection, symbol: str
) -> TradingPlan | None:
    rows = connection.execute(
        """
        SELECT payload_json
        FROM trading_plans
        ORDER BY generated_at DESC, rowid DESC
        """
    ).fetchall()
    for row in rows:
        plan = TradingPlan.model_validate_json(row["payload_json"])
        if _plan_contains_symbol(plan, symbol):
            return plan
    return None


def _decision_for_snapshot(
    connection: sqlite3.Connection,
    snapshot_id: int,
    symbol: str,
) -> tuple[TradingPlan | None, Recommendation | None]:
    plan = None
    rows = connection.execute(
        """
        SELECT payload_json
        FROM trading_plans
        ORDER BY generated_at DESC, rowid DESC
        """
    ).fetchall()
    for row in rows:
        candidate = TradingPlan.model_validate_json(row["payload_json"])
        if candidate.market_input_snapshot_id == snapshot_id and _plan_contains_symbol(
            candidate, symbol
        ):
            plan = candidate
            break

    recommendation = None
    rows = connection.execute(
        """
        SELECT payload_json
        FROM recommendations
        WHERE symbol = ?
        ORDER BY data_time DESC, created_at DESC, rowid DESC
        """,
        (symbol,),
    ).fetchall()
    for row in rows:
        candidate = Recommendation.model_validate_json(row["payload_json"])
        if candidate.market_input_snapshot_id == snapshot_id:
            recommendation = candidate
            break
    return plan, recommendation


def _latest_market_snapshot_for_symbol(
    connection: sqlite3.Connection, symbol: str
) -> tuple[int | None, MarketInputSnapshot | None]:
    rows = connection.execute(
        """
        SELECT id, payload_json
        FROM market_input_snapshots
        ORDER BY id DESC
        """
    ).fetchall()
    universe_repository = UniverseSnapshotRepository(connection)
    for row in rows:
        snapshot = MarketInputSnapshot.model_validate_json(row["payload_json"])
        reference_symbols = set(snapshot.quote_snapshot_refs)
        reference_symbols.update(snapshot.history_snapshot_refs)
        reference_symbols.update(snapshot.money_flow_snapshot_refs)
        reference_symbols.update(snapshot.intraday_strength_snapshot_refs)
        reference_symbols.update(snapshot.dataset_quality)
        if symbol in reference_symbols:
            return int(row["id"]), snapshot
        universe = universe_repository.get(snapshot.universe_snapshot_id)
        if universe is not None and any(
            member.symbol == symbol for member in universe.members
        ):
            return int(row["id"]), snapshot
    return None, None


def _strength_components(
    snapshot: IntradayStrengthSnapshot,
) -> list[MarketStrengthComponent]:
    return [
        MarketStrengthComponent(
            key=component.name,
            label=component.name,
            value=component.value,
            status=(
                "complete"
                if component.status is ComponentStatus.AVAILABLE
                else "unavailable"
            ),
            direction=component.direction,
            reason=component.reason,
        )
        for component in snapshot.components
    ]


def _quality_status(
    quote: QuoteSnapshot | None,
    strength: IntradayStrengthSnapshot | None,
) -> QualityStatus:
    if quote is None:
        return "unavailable"
    if quote.status is QuoteStatus.FAILED:
        return "failed"
    if quote.status is QuoteStatus.STALE:
        return "stale"
    if quote.status is QuoteStatus.PARTIAL:
        return "partial"
    if strength is None or strength.degraded:
        return "partial"
    return "complete"


def _merge_persisted_quality(
    status: QualityStatus,
    warnings: list[str],
    quality_by_dataset: dict[CaptureDataset, DatasetQuality],
) -> tuple[QualityStatus, list[str]]:
    """Overlay latest capture quality without hiding already stored market facts."""
    failed = False
    stale = False
    degraded = False
    merged_warnings = list(warnings)
    for dataset, quality in sorted(
        quality_by_dataset.items(), key=lambda item: item[0].value
    ):
        if quality.status in {
            CaptureResultStatus.COMPLETE,
            CaptureResultStatus.NOT_APPLICABLE,
        }:
            continue
        merged_warnings.extend(
            [
                f"persisted {dataset.value} quality is {quality.status.value}",
                quality.warning,
            ]
        )
        failed = failed or quality.status is CaptureResultStatus.FAILED
        stale = stale or quality.status is CaptureResultStatus.STALE
        degraded = degraded or quality.status is CaptureResultStatus.DEGRADED

    if failed:
        status = "failed"
    elif stale:
        status = "stale"
    elif degraded:
        status = "degraded"
    return status, _deduplicate(merged_warnings)


def _unread_count(connection: sqlite3.Connection, symbol: str) -> int:
    row = connection.execute(
        "SELECT COUNT(*) AS count FROM notifications WHERE symbol = ? AND status = ?",
        (symbol, "unread"),
    ).fetchone()
    return int(row["count"])


def _scanner_item(
    connection: sqlite3.Connection,
    member: UniverseMember,
    plan: TradingPlan | None,
) -> MarketSymbolSummary:
    quote = QuoteSnapshotRepository(connection).latest_for_symbol(member.symbol)
    strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
        member.symbol
    )
    _, market_snapshot = _latest_market_snapshot_for_symbol(connection, member.symbol)
    recommendation = RecommendationRepository(connection).latest_for_symbol(
        member.symbol
    )
    applicable_plan = (
        plan
        if plan is not None and _plan_contains_symbol(plan, member.symbol)
        else None
    )
    warnings: list[str] = []
    if quote is None:
        warnings.append("quote data unavailable")
    elif quote.warning:
        warnings.append(quote.warning)
    if strength is None:
        warnings.append("intraday strength unavailable")
    elif strength.degraded:
        warnings.extend(strength.degradation_reasons)
    status, warnings = _merge_persisted_quality(
        _quality_status(quote, strength),
        warnings,
        {}
        if market_snapshot is None
        else market_snapshot.dataset_quality.get(member.symbol, {}),
    )
    return MarketSymbolSummary(
        symbol=member.symbol,
        name=member.name,
        sources=[source.value for source in member.sources],
        current_price=None if quote is None else quote.current_price,
        change_pct=None if quote is None else quote.change_pct,
        recommendation_action=(
            None if recommendation is None else recommendation.action.value
        ),
        intraday_strength=("unavailable" if strength is None else strength.label.value),
        plan_status=None if applicable_plan is None else applicable_plan.status.value,
        quality_status=status,
        unread_count=_unread_count(connection, member.symbol),
        data_time=None if quote is None else quote.data_time,
        warnings=_deduplicate(warnings),
    )


@router.get("/symbols", response_model=PaginatedMarketSymbols)
def list_market_symbols(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=250)] = 50,
    container: ApiContainer = Depends(get_container),
) -> PaginatedMarketSymbols:
    with connection_scope(container.settings) as connection:
        metadata = {
            item.symbol: item for item in InstrumentRepository(connection).list_active()
        }
        current_members = build_universe(
            positions=PositionRepository(connection).list(),
            watchlist=WatchPinnedRepository(connection).list(),
            instrument_metadata=metadata,
            created_at=_now(),
        )
        decision_members = [
            member for member in current_members if member.plan_enabled
        ]
        total = len(decision_members)
        start = (page - 1) * page_size
        items = [
            _scanner_item(
                connection,
                member,
                _latest_plan_for_symbol(connection, member.symbol),
            )
            for member in decision_members[start : start + page_size]
        ]
        return PaginatedMarketSymbols(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )


def _position_overview(
    position: Position, current_price: float | None
) -> PositionOverview:
    floating_pnl_pct = None
    if current_price is not None:
        floating_pnl_pct = (current_price - position.cost_price) / position.cost_price
    return PositionOverview(
        quantity=position.quantity,
        available_quantity=position.available_quantity,
        cost_price=position.cost_price,
        floating_pnl_pct=floating_pnl_pct,
    )


def _plan_overview(plan: TradingPlan, symbol: str) -> PlanOverview:
    context = plan.symbol_contexts.get(symbol)
    allowed_actions = (
        list(context.allowed_actions)
        if context is not None
        else list(plan.candidate_actions.get(symbol, []))
    )
    invalid_if = (
        list(context.invalid_if)
        if context is not None
        else list(plan.invalid_if.get(symbol, []))
    )
    return PlanOverview(
        plan_id=plan.plan_id,
        status=plan.status.value,
        allowed_actions=allowed_actions,
        invalid_if=invalid_if,
        valid_until=plan.valid_until,
    )


def _market_structure(
    quote: QuoteSnapshot | None, bars: list[DailyBar]
) -> MarketStructureOverview | None:
    if not bars:
        return None
    current_price = (
        quote.current_price
        if quote is not None and quote.current_price is not None
        else bars[-1].close
    )
    features = calculate_daily_features(bars)
    structure = select_market_structure(current_price, bars, features)
    trend = (
        str(features.ma5_slope.value) if features.ma5_slope.available else "unavailable"
    )
    return MarketStructureOverview(
        support=structure.support,
        resistance=structure.resistance,
        atr14=structure.atr14,
        trend=trend,
        reason="; ".join(structure.reasons),
    )


@router.get("/symbols/{symbol}/overview", response_model=MarketOverview)
def get_market_overview(
    symbol: SymbolPath,
    container: ApiContainer = Depends(get_container),
) -> MarketOverview:
    with connection_scope(container.settings) as connection:
        quote = QuoteSnapshotRepository(connection).latest_for_symbol(symbol)
        strength = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            symbol
        )
        stored_bars = DailyBarRepository(connection).current(symbol, limit=250)
        bars = [stored.bar for stored in stored_bars]
        position = PositionRepository(connection).get(symbol)
        plan = _latest_plan_for_symbol(connection, symbol)
        recommendation = RecommendationRepository(connection).latest_for_symbol(symbol)
        snapshot_id, snapshot = _latest_market_snapshot_for_symbol(connection, symbol)
        universe = UniverseSnapshotRepository(connection).latest()
        member = (
            None
            if universe is None
            else next(
                (item for item in universe.members if item.symbol == symbol), None
            )
        )
        name = (
            member.name
            if member is not None
            else quote.name
            if quote is not None and quote.name
            else position.name
            if position is not None
            else symbol
        )

        warnings = [] if snapshot is None else list(snapshot.warnings)
        if quote is None:
            warnings.append("quote data unavailable")
        elif quote.warning:
            warnings.append(quote.warning)
        if strength is None:
            warnings.append("intraday strength unavailable")
        elif strength.degraded:
            warnings.extend(strength.degradation_reasons)
        if not bars:
            warnings.append("daily bar data unavailable")

        status = _quality_status(quote, strength)
        if quote is None and (bars or strength is not None):
            status = "partial"
        elif status == "complete" and (not bars or warnings):
            status = "partial"
        status, warnings = _merge_persisted_quality(
            status,
            warnings,
            {} if snapshot is None else snapshot.dataset_quality.get(symbol, {}),
        )
        fetched_times = [
            value
            for value in (
                None if quote is None else quote.fetched_at,
                None if strength is None else strength.fetched_at,
                None if not bars else max(bar.fetched_at for bar in bars),
                None if snapshot is None else snapshot.fetched_at,
            )
            if value is not None
        ]
        risks = []
        if plan is not None:
            risks.extend(plan.invalid_if.get(symbol, []))
            risks.extend(plan.warnings)
            context = plan.symbol_contexts.get(symbol)
            if context is not None:
                risks.extend(context.risks)
                risks.extend(context.invalid_if)
        risks.extend(warnings)

        return MarketOverview(
            symbol=symbol,
            name=name,
            snapshot_id=snapshot_id,
            status=status,
            data_time=None if quote is None else quote.data_time,
            fetched_at=max(fetched_times) if fetched_times else _now(),
            warnings=_deduplicate(warnings),
            position=(
                None
                if position is None
                else _position_overview(
                    position, None if quote is None else quote.current_price
                )
            ),
            plan=None if plan is None else _plan_overview(plan, symbol),
            recommendation=(
                None
                if recommendation is None
                else RecommendationOverview(
                    recommendation_id=recommendation.recommendation_id,
                    action=recommendation.action.value,
                    confidence=recommendation.confidence,
                    reason=recommendation.reason,
                    data_time=recommendation.data_time,
                )
            ),
            market_structure=_market_structure(quote, bars),
            intraday_strength=(
                None
                if strength is None
                else StrengthOverview(
                    label=strength.label.value,
                    confidence=strength.confidence.value,
                    components=_strength_components(strength),
                    degraded_reason=(
                        "; ".join(strength.degradation_reasons)
                        if strength.degradation_reasons
                        else None
                    ),
                )
            ),
            risks=_deduplicate(risks),
        )


def _rolling_mean(bars: list[DailyBar], index: int, window: int) -> float | None:
    if index + 1 < window:
        return None
    values = [bar.close for bar in bars[index - window + 1 : index + 1]]
    return sum(values) / window


def _latest_dataset_quality(
    connection: sqlite3.Connection,
    symbol: str,
    dataset: CaptureDataset,
) -> DatasetQuality | None:
    rows = connection.execute(
        "SELECT payload_json FROM market_input_snapshots ORDER BY id DESC"
    ).fetchall()
    for row in rows:
        aggregate = MarketInputSnapshot.model_validate_json(row["payload_json"])
        quality = aggregate.dataset_quality.get(symbol, {}).get(dataset)
        if quality is not None:
            return quality
        if dataset is CaptureDataset.DAILY_BAR:
            reference_id = aggregate.history_snapshot_refs.get(symbol)
            snapshot = (
                None
                if reference_id is None
                else HistorySnapshotRepository(connection).get(reference_id)
            )
        elif dataset is CaptureDataset.MONEY_FLOW:
            reference_id = aggregate.money_flow_snapshot_refs.get(symbol)
            snapshot = (
                None
                if reference_id is None
                else MoneyFlowSnapshotRepository(connection).get(reference_id)
            )
        else:
            snapshot = None
        if snapshot is not None:
            return DatasetQuality(
                status=snapshot.status,
                data_start=snapshot.data_start,
                data_end=snapshot.data_end,
                actual_rows=snapshot.row_count,
                warning=snapshot.warning,
            )
    return None


def _time_series_quality(
    dates: list[date],
    *,
    requested_rows: int,
    quality: DatasetQuality | None,
    coverage_label: str,
    range_label: str,
) -> tuple[QualityStatus, list[str]]:
    if not dates:
        return "unavailable", [f"{range_label} data unavailable"]

    warnings: list[str] = []
    if len(dates) < requested_rows:
        warnings.append(
            f"requested {requested_rows} {coverage_label}, found {len(dates)}"
        )

    expected_dates = XSHGTradingCalendar().trading_days(dates[0], dates[-1])
    missing_count = len(set(expected_dates) - set(dates))
    if missing_count:
        unit = "day" if missing_count == 1 else "days"
        warnings.append(
            f"{range_label} range is missing {missing_count} XSHG trading {unit}"
        )

    if quality is not None:
        if quality.status.value != "complete":
            warnings.append(
                f"persisted {range_label} quality is {quality.status.value}"
            )
        if quality.warning:
            warnings.append(quality.warning)
        if quality.data_end is not None and dates[-1] != quality.data_end:
            warnings.append(
                f"latest {range_label} date {dates[-1].isoformat()} does not match "
                f"quality end {quality.data_end.isoformat()}"
            )
        if (
            len(dates) < requested_rows
            and quality.data_start is not None
            and dates[0] != quality.data_start
        ):
            warnings.append(
                f"earliest {range_label} date {dates[0].isoformat()} does not match "
                f"quality start {quality.data_start.isoformat()}"
            )
        expected_from_quality = (
            min(requested_rows, quality.expected_rows)
            if quality.expected_rows > 0
            else 0
        )
        if expected_from_quality and quality.actual_rows < expected_from_quality:
            warnings.append(
                f"persisted {range_label} quality covers "
                f"{quality.actual_rows}/{expected_from_quality} requested rows"
            )

    warnings = _deduplicate(warnings)
    if quality is not None and quality.status in {
        CaptureResultStatus.STALE,
        CaptureResultStatus.FAILED,
    }:
        return quality.status.value, warnings
    return ("degraded" if warnings else "complete"), warnings


def _empty_dataset_quality(
    quality: DatasetQuality | None,
    *,
    unavailable_warning: str,
    range_label: str,
) -> tuple[QualityStatus, list[str]]:
    """Keep persisted terminal quality visible even when no rows were stored."""
    if quality is None:
        return "unavailable", [unavailable_warning]

    if quality.status is CaptureResultStatus.NOT_APPLICABLE:
        return "not_applicable", []

    warnings = _deduplicate(
        [
            unavailable_warning,
            f"persisted {range_label} quality is {quality.status.value}",
            quality.warning,
        ]
    )
    if quality.status in {
        CaptureResultStatus.STALE,
        CaptureResultStatus.FAILED,
        CaptureResultStatus.DEGRADED,
    }:
        return quality.status.value, warnings
    return "unavailable", warnings


@router.get("/symbols/{symbol}/daily-bars", response_model=DailyBarsResponse)
def get_daily_bars(
    symbol: SymbolPath,
    limit: Annotated[int, Query(ge=1, le=250)] = 120,
    container: ApiContainer = Depends(get_container),
) -> DailyBarsResponse:
    with connection_scope(container.settings) as connection:
        stored = DailyBarRepository(connection).current(symbol, limit=limit + 59)
        bars = [item.bar for item in stored]
        quality = _latest_dataset_quality(connection, symbol, CaptureDataset.DAILY_BAR)
    if not bars:
        status, warnings = _empty_dataset_quality(
            quality,
            unavailable_warning="daily bar data unavailable",
            range_label="daily bar",
        )
        return DailyBarsResponse(
            symbol=symbol,
            status=status,
            data_time=None,
            fetched_at=_now(),
            warnings=warnings,
            bars=[],
        )

    start = max(0, len(bars) - limit)
    response_bars = [
        DailyBarResponse(
            trade_date=bar.trade_date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            amount=bar.amount,
            ma5=_rolling_mean(bars, index, 5),
            ma10=_rolling_mean(bars, index, 10),
            ma20=_rolling_mean(bars, index, 20),
            ma60=_rolling_mean(bars, index, 60),
        )
        for index, bar in enumerate(bars)
        if index >= start
    ]
    source_times = [
        bar.source_updated_at for bar in bars if bar.source_updated_at is not None
    ]
    response_dates = [item.trade_date for item in response_bars]
    status, warnings = _time_series_quality(
        response_dates,
        requested_rows=limit,
        quality=quality,
        coverage_label="daily bars",
        range_label="daily bar",
    )
    return DailyBarsResponse(
        symbol=symbol,
        status=status,
        data_time=(
            max(source_times)
            if source_times
            else None
            if quality is None
            else quality.data_time
        ),
        fetched_at=max(bar.fetched_at for bar in bars),
        warnings=warnings,
        bars=response_bars,
    )


@router.get("/symbols/{symbol}/money-flow", response_model=MoneyFlowResponse)
def get_money_flow(
    symbol: SymbolPath,
    limit: Annotated[int, Query(ge=1, le=60)] = 60,
    container: ApiContainer = Depends(get_container),
) -> MoneyFlowResponse:
    with connection_scope(container.settings) as connection:
        quality = _latest_dataset_quality(connection, symbol, CaptureDataset.MONEY_FLOW)
        stored = (
            []
            if quality is not None
            and quality.status is CaptureResultStatus.NOT_APPLICABLE
            else MoneyFlowRepository(connection).current(symbol, limit=limit)
        )
    if not stored:
        status, warnings = _empty_dataset_quality(
            quality,
            unavailable_warning="money-flow data unavailable",
            range_label="money-flow",
        )
        return MoneyFlowResponse(
            symbol=symbol,
            status=status,
            data_time=None,
            fetched_at=_now(),
            warnings=warnings,
            rows=[],
        )
    flows = [item.flow for item in stored]
    source_times = [
        flow.source_updated_at for flow in flows if flow.source_updated_at is not None
    ]
    status, warnings = _time_series_quality(
        [flow.trade_date for flow in flows],
        requested_rows=limit,
        quality=quality,
        coverage_label="money-flow rows",
        range_label="money-flow",
    )
    return MoneyFlowResponse(
        symbol=symbol,
        status=status,
        data_time=(
            max(source_times)
            if source_times
            else None
            if quality is None
            else quality.data_time
        ),
        fetched_at=max(flow.fetched_at for flow in flows),
        warnings=warnings,
        rows=[
            MoneyFlowRow(
                trade_date=flow.trade_date,
                main_net_amount=flow.main_net_amount,
                main_net_ratio=flow.main_net_pct,
                super_large_net_amount=flow.super_large_net_amount,
                super_large_net_ratio=flow.super_large_net_pct,
                large_net_amount=flow.large_net_amount,
                large_net_ratio=flow.large_net_pct,
                medium_net_amount=flow.medium_net_amount,
                medium_net_ratio=flow.medium_net_pct,
                small_net_amount=flow.small_net_amount,
                small_net_ratio=flow.small_net_pct,
            )
            for flow in flows
        ],
    )


def _previous_close(
    connection: sqlite3.Connection, symbol: str, selected_date: date
) -> float | None:
    bars = [stored.bar for stored in DailyBarRepository(connection).current(symbol)]
    previous = [bar for bar in bars if bar.trade_date < selected_date]
    if previous:
        return previous[-1].close
    quote = QuoteSnapshotRepository(connection).latest_for_symbol(symbol)
    if (
        quote is None
        or quote.data_time is None
        or quote.data_time.astimezone(SHANGHAI).date() != selected_date
    ):
        return None
    return quote.previous_close


def _recommendation_markers(
    connection: sqlite3.Connection, symbol: str, selected_date: date
) -> list[RecommendationMarker]:
    recommendations = RecommendationRepository(connection).list(
        symbol=symbol, limit=250
    )
    markers = []
    for recommendation in recommendations:
        local_time = recommendation.data_time.astimezone(SHANGHAI)
        if local_time.date() != selected_date:
            continue
        price = recommendation.price_context.get("current_price")
        if not isinstance(price, int | float) or isinstance(price, bool) or price <= 0:
            continue
        markers.append(
            RecommendationMarker(
                time=local_time.strftime("%H:%M"),
                action=recommendation.action.value,
                price=float(price),
                recommendation_id=recommendation.recommendation_id,
            )
        )
    return sorted(markers, key=lambda marker: (marker.time, marker.recommendation_id))


@router.get("/symbols/{symbol}/minute-bars", response_model=MinuteBarsResponse)
def get_minute_bars(
    symbol: SymbolPath,
    trade_date: Annotated[date | None, Query()] = None,
    container: ApiContainer = Depends(get_container),
) -> MinuteBarsResponse:
    with connection_scope(container.settings) as connection:
        repository = MinuteBarRepository(connection)
        selected_date = trade_date
        if selected_date is None:
            trade_dates = repository.trade_dates(symbol)
            selected_date = trade_dates[-1] if trade_dates else None
        bars = (
            []
            if selected_date is None
            else repository.for_trade_date(symbol, selected_date)
        )
        previous_close = (
            None
            if selected_date is None
            else _previous_close(connection, symbol, selected_date)
        )
        markers = (
            []
            if selected_date is None
            else _recommendation_markers(connection, symbol, selected_date)
        )
        quality = _latest_dataset_quality(
            connection, symbol, CaptureDataset.MINUTE_BAR
        )

    if not bars:
        status, warnings = _empty_dataset_quality(
            quality,
            unavailable_warning="minute bar data unavailable",
            range_label="minute bar",
        )
        return MinuteBarsResponse(
            symbol=symbol,
            trade_date=selected_date,
            status=status,
            data_time=None,
            fetched_at=_now(),
            previous_close=previous_close,
            warnings=warnings,
            bars=[],
            recommendation_markers=markers,
        )

    cumulative_volume = 0.0
    cumulative_amount = 0.0
    response_bars = []
    for bar in bars:
        cumulative_volume += bar.volume
        cumulative_amount += bar.amount
        response_bars.append(
            MinuteBarResponse(
                minute=bar.minute.astimezone(SHANGHAI).strftime("%H:%M"),
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                amount=bar.amount,
                vwap=(
                    cumulative_amount / cumulative_volume
                    if cumulative_volume > 0
                    else None
                ),
            )
        )
    status: QualityStatus = "complete"
    warnings: list[str] = []
    if quality is not None and quality.status is not CaptureResultStatus.COMPLETE:
        status = quality.status.value
        warnings = _deduplicate(
            [
                f"persisted minute bar quality is {quality.status.value}",
                quality.warning,
            ]
        )
    return MinuteBarsResponse(
        symbol=symbol,
        trade_date=selected_date,
        status=status,
        data_time=max(bar.minute for bar in bars),
        fetched_at=max(bar.fetched_at for bar in bars),
        previous_close=previous_close,
        warnings=warnings,
        bars=response_bars,
        recommendation_markers=markers,
    )


@router.get(
    "/symbols/{symbol}/intraday-strength/latest",
    response_model=IntradayStrengthResponse,
)
def get_latest_intraday_strength(
    symbol: SymbolPath,
    container: ApiContainer = Depends(get_container),
) -> IntradayStrengthResponse:
    with connection_scope(container.settings) as connection:
        snapshot = IntradayStrengthSnapshotRepository(connection).latest_for_symbol(
            symbol
        )
        quality = _latest_dataset_quality(
            connection, symbol, CaptureDataset.INTRADAY_STRENGTH
        )
    if snapshot is None:
        status, warnings = _empty_dataset_quality(
            quality,
            unavailable_warning="intraday strength unavailable",
            range_label="intraday strength",
        )
        return IntradayStrengthResponse(
            symbol=symbol,
            status=status,
            label="unavailable",
            confidence="low",
            data_time=None,
            fetched_at=_now(),
            coverage_ratio=None,
            last_minute=None,
            degraded_reason="; ".join(warnings),
            rule_version="",
            components=[],
            warnings=warnings,
        )
    warnings = list(snapshot.degradation_reasons) if snapshot.degraded else []
    status, warnings = _merge_persisted_quality(
        "degraded" if snapshot.degraded else "complete",
        warnings,
        {} if quality is None else {CaptureDataset.INTRADAY_STRENGTH: quality},
    )
    return IntradayStrengthResponse(
        symbol=symbol,
        status=status,
        label=snapshot.label.value,
        confidence=snapshot.confidence.value,
        data_time=snapshot.data_time,
        fetched_at=snapshot.fetched_at,
        coverage_ratio=snapshot.data_coverage,
        last_minute=(
            None
            if snapshot.last_minute is None
            else snapshot.last_minute.astimezone(SHANGHAI).strftime("%H:%M")
        ),
        degraded_reason="; ".join(warnings) if warnings else None,
        rule_version=snapshot.rule_version,
        components=_strength_components(snapshot),
        warnings=warnings,
    )


@router.get("/runs", response_model=PaginatedMarketRuns)
def list_market_runs(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=250)] = 50,
    container: ApiContainer = Depends(get_container),
) -> PaginatedMarketRuns:
    offset = (page - 1) * page_size
    with connection_scope(container.settings) as connection:
        total_row = connection.execute(
            "SELECT COUNT(*) AS count FROM market_capture_runs"
        ).fetchone()
        rows = connection.execute(
            """
            SELECT
              run_id, workflow_type, trade_date, period_start, period_end,
              idempotency_key, status, started_at, finished_at,
              requested_symbols, processed_symbols, provider_calls,
              provider_duration_ms, rows_received, rows_written, cleaned_rows,
              plan_count, recommendation_count, notification_count,
              email_outbox_count, retry_count, warning_count, failure_count,
              error_summary
            FROM market_capture_runs
            ORDER BY started_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
        run_repository = MarketCaptureResultRepository(connection)
        items = []
        for row in rows:
            run = MarketCaptureRun.model_validate(dict(row))
            results = run_repository.list_for_run(run.run_id)
            items.append(
                MarketRunSummary(
                    **run.model_dump(exclude_computed_fields=True),
                    dataset_counts=_capture_dataset_counts(results),
                )
            )
    return PaginatedMarketRuns(
        items=items,
        total=int(total_row["count"]),
        page=page,
        page_size=page_size,
    )


@router.get("/runs/{run_id}", response_model=MarketRunDetail)
def get_market_run(
    run_id: Annotated[str, ApiPath(min_length=1, max_length=500)],
    container: ApiContainer = Depends(get_container),
) -> MarketRunDetail:
    with connection_scope(container.settings) as connection:
        run = MarketCaptureRunRepository(connection).get(run_id)
        if run is None:
            raise _run_not_found(run_id)
        results = MarketCaptureResultRepository(connection).list_for_run(run_id)
    return MarketRunDetail(
        **run.model_dump(exclude_computed_fields=True),
        dataset_counts=_capture_dataset_counts(results),
        results=results,
    )


def _quality_dataset(
    dataset: Literal["quote", "history", "money_flow", "intraday_strength"],
    quality: DatasetQuality | None,
) -> MarketTraceDataset:
    if quality is None:
        return MarketTraceDataset(
            dataset=dataset,
            reference_id=None,
            status="unavailable",
            source="",
            data_start=None,
            data_end=None,
            data_time=None,
            fetched_at=None,
            warnings=[f"{dataset} dataset unavailable"],
        )
    return MarketTraceDataset(
        dataset=dataset,
        reference_id=None,
        status=quality.status.value,
        source=quality.source,
        data_start=None
        if quality.data_start is None
        else quality.data_start.isoformat(),
        data_end=None if quality.data_end is None else quality.data_end.isoformat(),
        data_time=quality.data_time,
        fetched_at=None,
        warnings=(
            []
            if quality.status is CaptureResultStatus.NOT_APPLICABLE
            else [quality.warning]
            if quality.warning
            else [f"{dataset} reference unavailable"]
        ),
    )


def _merge_trace_quality(
    status: QualityStatus,
    warnings: list[str],
    dataset: CaptureDataset,
    quality: DatasetQuality | None,
) -> tuple[QualityStatus, list[str]]:
    return _merge_persisted_quality(
        status,
        warnings,
        {} if quality is None else {dataset: quality},
    )


def _quote_trace(
    repository: QuoteSnapshotRepository,
    reference_id: int | None,
    quality: DatasetQuality | None,
) -> MarketTraceDataset:
    quote = None if reference_id is None else repository.get(reference_id)
    if quote is None:
        return _quality_dataset("quote", quality)
    status: QualityStatus = (
        "complete" if quote.status is QuoteStatus.OK else quote.status.value
    )
    status, warnings = _merge_trace_quality(
        status,
        [quote.warning] if quote.warning else [],
        CaptureDataset.QUOTE,
        quality,
    )
    return MarketTraceDataset(
        dataset="quote",
        reference_id=reference_id,
        status=status,
        source=quote.source,
        data_start=None,
        data_end=None,
        data_time=quote.data_time,
        fetched_at=quote.fetched_at,
        warnings=warnings,
    )


def _history_trace(
    repository: HistorySnapshotRepository,
    reference_id: int | None,
    quality: DatasetQuality | None,
) -> MarketTraceDataset:
    snapshot: HistorySnapshot | None = (
        None if reference_id is None else repository.get(reference_id)
    )
    if snapshot is None:
        return _quality_dataset("history", quality)
    members = repository.members(reference_id)
    source = members[-1].bar.source if members else ""
    source_times = [
        member.bar.source_updated_at
        for member in members
        if member.bar.source_updated_at is not None
    ]
    status, warnings = _merge_trace_quality(
        snapshot.status.value,
        [snapshot.warning] if snapshot.warning else [],
        CaptureDataset.DAILY_BAR,
        quality,
    )
    return MarketTraceDataset(
        dataset="history",
        reference_id=reference_id,
        status=status,
        source=source,
        data_start=None
        if snapshot.data_start is None
        else snapshot.data_start.isoformat(),
        data_end=None if snapshot.data_end is None else snapshot.data_end.isoformat(),
        data_time=max(source_times) if source_times else None,
        fetched_at=snapshot.fetched_at,
        warnings=warnings,
    )


def _money_flow_trace(
    repository: MoneyFlowSnapshotRepository,
    reference_id: int | None,
    quality: DatasetQuality | None,
) -> MarketTraceDataset:
    snapshot: MoneyFlowSnapshot | None = (
        None if reference_id is None else repository.get(reference_id)
    )
    if snapshot is None:
        return _quality_dataset("money_flow", quality)
    members = repository.members(reference_id)
    source = members[-1].flow.source if members else ""
    source_times = [
        member.flow.source_updated_at
        for member in members
        if member.flow.source_updated_at is not None
    ]
    status, warnings = _merge_trace_quality(
        snapshot.status.value,
        [snapshot.warning] if snapshot.warning else [],
        CaptureDataset.MONEY_FLOW,
        quality,
    )
    return MarketTraceDataset(
        dataset="money_flow",
        reference_id=reference_id,
        status=status,
        source=source,
        data_start=None
        if snapshot.data_start is None
        else snapshot.data_start.isoformat(),
        data_end=None if snapshot.data_end is None else snapshot.data_end.isoformat(),
        data_time=max(source_times) if source_times else None,
        fetched_at=snapshot.fetched_at,
        warnings=warnings,
    )


def _strength_trace(
    repository: IntradayStrengthSnapshotRepository,
    reference_id: int | None,
    quality: DatasetQuality | None,
) -> MarketTraceDataset:
    snapshot = None if reference_id is None else repository.get(reference_id)
    if snapshot is None:
        return _quality_dataset("intraday_strength", quality)
    warnings = list(snapshot.degradation_reasons) if snapshot.degraded else []
    first_minute = getattr(snapshot, "first_minute", None)
    if first_minute is None and (
        snapshot.last_minute is not None or snapshot.data_coverage > 0
    ):
        try:
            first_minute = XSHGTradingCalendar().session(snapshot.trade_date).open_at
        except ValueError:
            first_minute = None
    data_end = snapshot.last_minute
    if data_end is None and snapshot.data_coverage > 0:
        data_end = snapshot.data_time
    status, warnings = _merge_trace_quality(
        "degraded" if snapshot.degraded else "complete",
        warnings,
        CaptureDataset.INTRADAY_STRENGTH,
        quality,
    )
    return MarketTraceDataset(
        dataset="intraday_strength",
        reference_id=reference_id,
        status=status,
        source=snapshot.source,
        data_start=None if first_minute is None else first_minute.isoformat(),
        data_end=None if data_end is None else data_end.isoformat(),
        data_time=snapshot.data_time,
        fetched_at=snapshot.fetched_at,
        warnings=warnings,
    )


def _trace_status(datasets: list[MarketTraceDataset]) -> QualityStatus:
    statuses = [dataset.status for dataset in datasets]
    if all(
        (dataset.status == "complete" and dataset.reference_id is not None)
        or dataset.status == "not_applicable"
        for dataset in datasets
    ):
        return "complete"
    if all(status == "unavailable" for status in statuses):
        return "unavailable"
    available = [status for status in statuses if status != "unavailable"]
    if available and all(status == "failed" for status in available):
        return "failed"
    if (
        available
        and all(status in ("complete", "stale") for status in available)
        and "stale" in available
    ):
        return "stale"
    return "partial"


@router.get(
    "/snapshots/{snapshot_id}/trace",
    response_model=MarketSnapshotTrace,
)
def get_market_snapshot_trace(
    snapshot_id: SnapshotIdPath,
    symbol: SymbolQuery,
    container: ApiContainer = Depends(get_container),
) -> MarketSnapshotTrace:
    with connection_scope(container.settings) as connection:
        snapshot = MarketInputSnapshotRepository(connection).get(snapshot_id)
        if snapshot is None:
            raise _snapshot_not_found(snapshot_id)
        quality = snapshot.dataset_quality.get(symbol, {})
        datasets = [
            _quote_trace(
                QuoteSnapshotRepository(connection),
                snapshot.quote_snapshot_refs.get(symbol),
                quality.get(CaptureDataset.QUOTE),
            ),
            _history_trace(
                HistorySnapshotRepository(connection),
                snapshot.history_snapshot_refs.get(symbol),
                quality.get(CaptureDataset.DAILY_BAR),
            ),
            _money_flow_trace(
                MoneyFlowSnapshotRepository(connection),
                snapshot.money_flow_snapshot_refs.get(symbol),
                quality.get(CaptureDataset.MONEY_FLOW),
            ),
            _strength_trace(
                IntradayStrengthSnapshotRepository(connection),
                snapshot.intraday_strength_snapshot_refs.get(symbol),
                quality.get(CaptureDataset.INTRADAY_STRENGTH),
            ),
        ]
        plan, recommendation = _decision_for_snapshot(connection, snapshot_id, symbol)

    dataset_warnings = [warning for dataset in datasets for warning in dataset.warnings]
    return MarketSnapshotTrace(
        symbol=symbol,
        instrument=snapshot.instrument_metadata.get(symbol),
        run_id=snapshot.capture_run_id,
        snapshot_id=snapshot_id,
        plan_id=None if plan is None else plan.plan_id,
        recommendation_id=(
            None if recommendation is None else recommendation.recommendation_id
        ),
        audit_id=None if recommendation is None else recommendation.audit_id,
        data_time=snapshot.data_time,
        fetched_at=snapshot.fetched_at,
        status=_trace_status(datasets),
        warnings=_deduplicate([*snapshot.warnings, *dataset_warnings]),
        thresholds=snapshot.thresholds,
        datasets=datasets,
    )
