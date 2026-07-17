from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    ComponentStatus,
    DailyBar,
    IntradayStrengthSnapshot,
    MIN_HISTORY_ROWS,
    MinuteBar,
    QuoteSnapshot,
    StrengthComponent,
    StrengthConfidence,
    StrengthLabel,
)


@dataclass(frozen=True)
class FeatureMetric:
    value: float | int | str | None
    available: bool
    reason: str


@dataclass(frozen=True)
class DailyFeatures:
    ma5: FeatureMetric
    ma10: FeatureMetric
    ma20: FeatureMetric
    ma60: FeatureMetric
    return_5: FeatureMetric
    return_10: FeatureMetric
    return_20: FeatureMetric
    return_60: FeatureMetric
    ma5_slope: FeatureMetric
    ma10_slope: FeatureMetric
    ma20_slope: FeatureMetric
    high20: FeatureMetric
    low20: FeatureMetric
    position20: FeatureMetric
    atr14: FeatureMetric
    average_volume5: FeatureMetric
    average_volume20: FeatureMetric
    volume_ratio: FeatureMetric
    high5: FeatureMetric
    low5: FeatureMetric
    high10: FeatureMetric
    low10: FeatureMetric


@dataclass(frozen=True)
class MarketStructure:
    support: float | None
    resistance: float | None
    atr14: float | None
    support_source: str | None
    resistance_source: str | None
    reasons: list[str]


def _available(value: float | int | str, reason: str) -> FeatureMetric:
    return FeatureMetric(value=value, available=True, reason=reason)


def _unavailable(reason: str) -> FeatureMetric:
    return FeatureMetric(value=None, available=False, reason=reason)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def calculate_daily_features(bars: list[DailyBar]) -> DailyFeatures:
    ordered = sorted(bars, key=lambda bar: bar.trade_date)
    if len({bar.trade_date for bar in ordered}) != len(ordered):
        raise ValueError("daily feature input contains duplicate trade dates")
    if len({bar.symbol for bar in ordered}) > 1:
        raise ValueError("daily feature input must contain one symbol")

    def ma(window: int) -> FeatureMetric:
        if len(ordered) < window:
            return _unavailable(f"requires {window} daily bars")
        return _available(_mean([bar.close for bar in ordered[-window:]]), f"MA{window}")

    def period_return(window: int) -> FeatureMetric:
        if len(ordered) < window + 1:
            return _unavailable(f"requires {window + 1} daily bars")
        value = (ordered[-1].close / ordered[-window - 1].close - 1) * 100
        return _available(value, f"{window}-trading-day return")

    def slope(window: int) -> FeatureMetric:
        if len(ordered) < window + 1:
            return _unavailable(f"requires {window + 1} daily bars")
        previous = _mean([bar.close for bar in ordered[-window - 1 : -1]])
        current = _mean([bar.close for bar in ordered[-window:]])
        direction = "up" if current > previous else "down" if current < previous else "flat"
        return _available(direction, f"MA{window} current={current:.6f}, previous={previous:.6f}")

    def rolling_high(window: int) -> FeatureMetric:
        if len(ordered) < window:
            return _unavailable(f"requires {window} daily bars")
        return _available(max(bar.high for bar in ordered[-window:]), f"{window}-day high")

    def rolling_low(window: int) -> FeatureMetric:
        if len(ordered) < window:
            return _unavailable(f"requires {window} daily bars")
        return _available(min(bar.low for bar in ordered[-window:]), f"{window}-day low")

    def average_volume(window: int) -> FeatureMetric:
        if len(ordered) < window:
            return _unavailable(f"requires {window} daily bars")
        return _available(
            _mean([bar.volume for bar in ordered[-window:]]),
            f"{window}-day average volume",
        )

    ma5 = ma(5)
    ma10 = ma(10)
    ma20 = ma(MIN_HISTORY_ROWS)
    ma60 = ma(60)
    high5 = rolling_high(5)
    low5 = rolling_low(5)
    high10 = rolling_high(10)
    low10 = rolling_low(10)
    high20 = rolling_high(MIN_HISTORY_ROWS)
    low20 = rolling_low(MIN_HISTORY_ROWS)
    if high20.available and low20.available:
        denominator = float(high20.value) - float(low20.value)
        position20 = (
            _available(
                (ordered[-1].close - float(low20.value)) / denominator,
                "current close position in 20-day range",
            )
            if denominator > 0
            else _unavailable("20-day high equals low")
        )
    else:
        position20 = _unavailable(
            f"requires {MIN_HISTORY_ROWS} daily bars"
        )

    if len(ordered) >= 15:
        true_ranges = [
            max(
                bar.high - bar.low,
                abs(bar.high - previous.close),
                abs(bar.low - previous.close),
            )
            for previous, bar in zip(ordered[-15:-1], ordered[-14:])
        ]
        atr14 = _available(_mean(true_ranges), "ATR14 using true range")
    else:
        atr14 = _unavailable("requires 15 daily bars")

    average_volume5 = average_volume(5)
    average_volume20 = average_volume(MIN_HISTORY_ROWS)
    if average_volume20.available and float(average_volume20.value) > 0:
        volume_ratio = _available(
            ordered[-1].volume / float(average_volume20.value),
            "current volume divided by 20-day average",
        )
    else:
        volume_ratio = _unavailable("20-day average volume is unavailable or zero")

    return DailyFeatures(
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma60=ma60,
        return_5=period_return(5),
        return_10=period_return(10),
        return_20=period_return(MIN_HISTORY_ROWS),
        return_60=period_return(60),
        ma5_slope=slope(5),
        ma10_slope=slope(10),
        ma20_slope=slope(MIN_HISTORY_ROWS),
        high20=high20,
        low20=low20,
        position20=position20,
        atr14=atr14,
        average_volume5=average_volume5,
        average_volume20=average_volume20,
        volume_ratio=volume_ratio,
        high5=high5,
        low5=low5,
        high10=high10,
        low10=low10,
    )


def select_market_structure(
    current_price: float,
    bars: list[DailyBar],
    features: DailyFeatures,
) -> MarketStructure:
    if current_price <= 0:
        raise ValueError("current_price must be positive")
    support_metrics = {
        "MA5": features.ma5,
        "MA10": features.ma10,
        "MA20": features.ma20,
        "5-day low": features.low5,
        "10-day low": features.low10,
        "20-day low": features.low20,
    }
    resistance_metrics = {
        "5-day high": features.high5,
        "10-day high": features.high10,
        "20-day high": features.high20,
    }
    support_candidates = [
        (name, float(metric.value))
        for name, metric in support_metrics.items()
        if metric.available and float(metric.value) < current_price
    ]
    resistance_candidates = [
        (name, float(metric.value))
        for name, metric in resistance_metrics.items()
        if metric.available and float(metric.value) > current_price
    ]
    support = max(support_candidates, key=lambda candidate: candidate[1], default=None)
    resistance = min(resistance_candidates, key=lambda candidate: candidate[1], default=None)
    reasons = []
    reasons.append(
        "no valid support candidate below current price"
        if support is None
        else f"nearest support below current price is {support[0]} at {support[1]:.6f}"
    )
    reasons.append(
        "no valid resistance candidate above current price"
        if resistance is None
        else f"nearest resistance above current price is {resistance[0]} at {resistance[1]:.6f}"
    )
    if features.atr14.available:
        reasons.append(f"ATR14 volatility buffer is {float(features.atr14.value):.6f}")
    else:
        reasons.append("ATR14 volatility buffer unavailable")
    return MarketStructure(
        support=None if support is None else support[1],
        resistance=None if resistance is None else resistance[1],
        atr14=None if not features.atr14.available else float(features.atr14.value),
        support_source=None if support is None else support[0],
        resistance_source=None if resistance is None else resistance[0],
        reasons=reasons,
    )


@dataclass(frozen=True)
class IntradayStrengthRules:
    previous_close_pct: float = 0.5
    open_pct: float = 0.3
    vwap_pct: float = 0.2
    momentum_5_pct: float = 0.3
    momentum_15_pct: float = 0.6
    position_high: float = 0.70
    position_low: float = 0.30
    volume_high: float = 1.5
    volume_low: float = 0.8
    rule_version: str = "intraday-strength-v1"

    def thresholds(self) -> dict[str, float]:
        return {
            "previous_close_pct": self.previous_close_pct,
            "open_pct": self.open_pct,
            "vwap_pct": self.vwap_pct,
            "momentum_5_pct": self.momentum_5_pct,
            "momentum_15_pct": self.momentum_15_pct,
            "position_high": self.position_high,
            "position_low": self.position_low,
            "volume_high": self.volume_high,
            "volume_low": self.volume_low,
        }


def _pct_change(current: float, baseline: float) -> float:
    return (current / baseline - 1) * 100


def _direction(value: float, threshold: float) -> int:
    if value >= threshold:
        return 1
    if value <= -threshold:
        return -1
    return 0


def _component(
    name: str,
    value: float | None,
    threshold: float | None,
    reason: str,
    *,
    direction: int = 0,
    source: str = "",
) -> StrengthComponent:
    return StrengthComponent(
        name=name,
        status=(ComponentStatus.AVAILABLE if value is not None else ComponentStatus.UNAVAILABLE),
        value=value,
        threshold=threshold,
        direction=direction if value is not None else 0,
        reason=reason,
        source=source,
    )


def calculate_intraday_strength(
    run_id: str,
    quote: QuoteSnapshot,
    minute_bars: list[MinuteBar],
    calendar: XSHGTradingCalendar,
    *,
    previous_daily_bar: DailyBar | None = None,
    rules: IntradayStrengthRules | None = None,
    fetched_at: datetime,
) -> IntradayStrengthSnapshot:
    rules = rules or IntradayStrengthRules()
    ordered = sorted(minute_bars, key=lambda bar: bar.minute)
    if any(bar.symbol != quote.symbol for bar in ordered):
        raise ValueError("minute bars and quote symbols must match")
    if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
        raise ValueError("strength fetched_at must be timezone-aware")
    if ordered:
        trade_date = ordered[-1].trade_date
        current = quote.current_price or ordered[-1].close
        data_time = ordered[-1].minute
    elif quote.data_time is not None:
        trade_date = quote.data_time.astimezone(calendar.timezone).date()
        current = quote.current_price
        data_time = quote.data_time
    else:
        raise ValueError("intraday strength requires a quote or minute data time")

    components: list[StrengthComponent] = []
    previous_close = quote.previous_close
    previous_close_source = "quote.previous_close"
    if previous_close is None and previous_daily_bar is not None:
        expected_previous = calendar.previous_trading_day(trade_date)
        if (
            previous_daily_bar.symbol == quote.symbol
            and previous_daily_bar.trade_date == expected_previous
        ):
            previous_close = previous_daily_bar.close
            previous_close_source = "previous forward-adjusted daily close"
    if current is not None and previous_close is not None:
        value = _pct_change(current, previous_close)
        components.append(
            _component(
                "previous_close_change",
                value,
                rules.previous_close_pct,
                f"current versus previous close from {previous_close_source}",
                direction=_direction(value, rules.previous_close_pct),
                source=previous_close_source,
            )
        )
    else:
        components.append(
            _component(
                "previous_close_change",
                None,
                rules.previous_close_pct,
                "current price or adjacent previous close unavailable",
            )
        )

    opening = ordered[0].open if ordered else quote.open_price
    if current is not None and opening is not None:
        value = _pct_change(current, opening)
        components.append(
            _component(
                "open_change",
                value,
                rules.open_pct,
                "current versus first valid minute open",
                direction=_direction(value, rules.open_pct),
                source="minute_bars",
            )
        )
    else:
        components.append(_component("open_change", None, rules.open_pct, "open unavailable"))

    total_volume = sum(bar.volume for bar in ordered)
    vwap = sum(bar.amount for bar in ordered) / total_volume if total_volume > 0 else None
    if current is not None and vwap is not None and vwap > 0:
        value = _pct_change(current, vwap)
        components.append(
            _component(
                "vwap_change",
                value,
                rules.vwap_pct,
                "current versus cumulative amount/volume VWAP",
                direction=_direction(value, rules.vwap_pct),
                source="minute_bars",
            )
        )
    else:
        components.append(_component("vwap_change", None, rules.vwap_pct, "VWAP unavailable"))

    momentum_directions: dict[int, int] = {}
    for window, threshold in ((5, rules.momentum_5_pct), (15, rules.momentum_15_pct)):
        if len(ordered) >= window + 1 and current is not None:
            value = _pct_change(current, ordered[-window - 1].close)
            direction = _direction(value, threshold)
            momentum_directions[window] = direction
            components.append(
                _component(
                    f"momentum_{window}",
                    value,
                    threshold,
                    f"current versus {window} complete trading minutes earlier",
                    direction=direction,
                    source="minute_bars",
                )
            )
        else:
            components.append(
                _component(
                    f"momentum_{window}",
                    None,
                    threshold,
                    f"requires {window + 1} complete minutes",
                )
            )

    if ordered and current is not None:
        session_high = max(bar.high for bar in ordered)
        session_low = min(bar.low for bar in ordered)
        denominator = session_high - session_low
        if denominator > 0:
            value = (current - session_low) / denominator
            direction = (
                1
                if value >= rules.position_high
                else -1
                if value <= rules.position_low
                else 0
            )
            components.append(
                _component(
                    "intraday_position",
                    value,
                    rules.position_high if direction >= 0 else rules.position_low,
                    "(current-low)/(high-low) from valid minute bars",
                    direction=direction,
                    source="minute_bars",
                )
            )
        else:
            components.append(
                _component(
                    "intraday_position", None, None, "intraday high equals low"
                )
            )
    else:
        components.append(_component("intraday_position", None, None, "minute range unavailable"))

    minute_volume_ratio: float | None = None
    if len(ordered) >= 25:
        recent = sum(bar.volume for bar in ordered[-5:]) / 5
        prior = sum(bar.volume for bar in ordered[-25:-5]) / 20
        if prior > 0:
            minute_volume_ratio = recent / prior
            volume_reason = "recent 5-minute average volume versus prior 20 minutes"
        else:
            volume_reason = "prior 20-minute average volume is zero"
    else:
        volume_reason = "requires 25 complete minutes"
    components.append(
        _component(
            "minute_volume_ratio",
            minute_volume_ratio,
            rules.volume_high,
            volume_reason,
            direction=0,
            source="minute_bars" if ordered else "",
        )
    )

    available = [
        component
        for component in components
        if component.name != "minute_volume_ratio" and component.available
    ]
    direction_sum = sum(component.direction for component in available)
    degraded = len(available) < 4
    degradation_reasons = [] if not degraded else ["fewer than four direction components available"]
    if degraded:
        label = StrengthLabel.NEUTRAL
    elif direction_sum >= 3 and vwap is not None and current is not None and current >= vwap:
        label = StrengthLabel.STRONG
    elif direction_sum <= -3 and vwap is not None and current is not None and current <= vwap:
        label = StrengthLabel.WEAK
    else:
        label = StrengthLabel.NEUTRAL

    if label is StrengthLabel.NEUTRAL:
        confidence = StrengthConfidence.LOW
    else:
        confidence = StrengthConfidence.MEDIUM
        expected_direction = 1 if label is StrengthLabel.STRONG else -1
        if minute_volume_ratio is None or minute_volume_ratio < rules.volume_low:
            confidence = StrengthConfidence.LOW
        elif (
            minute_volume_ratio >= rules.volume_high
            and momentum_directions.get(5) == expected_direction
        ):
            confidence = StrengthConfidence.HIGH

    expected_minutes = calendar.expected_minutes_through(data_time) if ordered else 0
    coverage = min(1.0, len(ordered) / expected_minutes) if expected_minutes else 0.0
    sources = sorted({bar.source for bar in ordered})
    return IntradayStrengthSnapshot(
        run_id=run_id,
        symbol=quote.symbol,
        trade_date=trade_date,
        label=label,
        confidence=confidence,
        degraded=degraded,
        degradation_reasons=degradation_reasons,
        components=components,
        direction_sum=direction_sum,
        minute_volume_ratio=minute_volume_ratio,
        thresholds=rules.thresholds(),
        rule_version=rules.rule_version,
        last_minute=None if not ordered else ordered[-1].minute,
        data_coverage=coverage,
        source=",".join(sources) if sources else quote.source,
        data_time=data_time,
        fetched_at=fetched_at,
    )
