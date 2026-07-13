from datetime import UTC, date, datetime, timedelta

import pytest

from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.features import (
    IntradayStrengthRules,
    calculate_daily_features,
    calculate_intraday_strength,
    select_market_structure,
)
from quantitative_trading.market.models import (
    DailyBar,
    MinuteBar,
    QuoteSnapshot,
    QuoteStatus,
    StrengthConfidence,
    StrengthLabel,
)


FETCHED_AT = datetime(2026, 7, 13, 7, 1, tzinfo=UTC)


def make_daily_bars(count: int) -> list[DailyBar]:
    start = date(2026, 1, 1)
    return [
        DailyBar(
            symbol="600000",
            trade_date=start + timedelta(days=index),
            open=10 + index * 0.1,
            high=11 + index * 0.1,
            low=9 + index * 0.1,
            close=10.5 + index * 0.1,
            volume=100 + index,
            amount=(100 + index) * (10.5 + index * 0.1),
            source="fake",
            fetched_at=FETCHED_AT,
        )
        for index in range(count)
    ]


def test_daily_features_use_exact_ma_return_atr_and_volume_windows() -> None:
    bars = make_daily_bars(61)

    features = calculate_daily_features(bars)

    assert features.ma5.value == pytest.approx(sum(bar.close for bar in bars[-5:]) / 5)
    assert features.ma60.value == pytest.approx(sum(bar.close for bar in bars[-60:]) / 60)
    assert features.return_5.value == pytest.approx((bars[-1].close / bars[-6].close - 1) * 100)
    true_ranges = [
        max(
            bar.high - bar.low,
            abs(bar.high - previous.close),
            abs(bar.low - previous.close),
        )
        for previous, bar in zip(bars[-15:-1], bars[-14:])
    ]
    assert features.atr14.value == pytest.approx(sum(true_ranges) / 14)
    assert features.volume_ratio.value == pytest.approx(
        bars[-1].volume / (sum(bar.volume for bar in bars[-20:]) / 20)
    )


def test_market_structure_selects_nearest_candidates_without_using_cost() -> None:
    bars = make_daily_bars(61)
    features = calculate_daily_features(bars)
    current = bars[-1].close

    structure = select_market_structure(current, bars, features)

    support_candidates = [
        value
        for value in (
            features.ma5.value,
            features.ma10.value,
            features.ma20.value,
            features.low5.value,
            features.low10.value,
            features.low20.value,
        )
        if value is not None and value < current
    ]
    assert structure.support == max(support_candidates)
    assert structure.atr14 == features.atr14.value
    assert "cost" not in " ".join(structure.reasons).lower()


def minute_series(*, rising: bool, volume_spike: bool) -> list[MinuteBar]:
    calendar = XSHGTradingCalendar()
    day = date(2026, 7, 13)
    session = calendar.session(day)
    minutes = []
    current = session.open_at
    index = 0
    while len(minutes) < 30:
        if calendar.is_trading_minute(current):
            price = 10 + (index * 0.01 if rising else -index * 0.01)
            volume = 100 if index < 25 or not volume_spike else 200
            minutes.append(
                MinuteBar(
                    symbol="600000",
                    trade_date=day,
                    minute=current,
                    open=price,
                    high=price + 0.02,
                    low=price - 0.02,
                    close=price,
                    volume=volume,
                    amount=price * volume,
                    source="fake",
                    fetched_at=FETCHED_AT,
                )
            )
            index += 1
        current += timedelta(minutes=1)
    return minutes


def test_intraday_strength_uses_exact_direction_and_confidence_thresholds() -> None:
    bars = minute_series(rising=True, volume_spike=True)
    quote = QuoteSnapshot(
        symbol="600000",
        previous_close=9.9,
        current_price=bars[-1].close,
        data_time=bars[-1].minute,
        fetched_at=FETCHED_AT,
        source="fake",
        status=QuoteStatus.PARTIAL,
        warning="optional quote fields unavailable",
    )

    snapshot = calculate_intraday_strength(
        "run-1",
        quote,
        bars,
        XSHGTradingCalendar(),
        rules=IntradayStrengthRules(),
        fetched_at=FETCHED_AT,
    )

    assert snapshot.label is StrengthLabel.STRONG
    assert snapshot.confidence is StrengthConfidence.HIGH
    assert snapshot.direction_sum >= 3
    assert snapshot.minute_volume_ratio == pytest.approx(2.0)
    assert snapshot.thresholds == {
        "previous_close_pct": 0.5,
        "open_pct": 0.3,
        "vwap_pct": 0.2,
        "momentum_5_pct": 0.3,
        "momentum_15_pct": 0.6,
        "position_high": 0.7,
        "position_low": 0.3,
        "volume_high": 1.5,
        "volume_low": 0.8,
    }


def test_intraday_strength_degrades_below_four_direction_components() -> None:
    bars = minute_series(rising=True, volume_spike=False)[:3]
    quote = QuoteSnapshot(
        symbol="600000",
        current_price=bars[-1].close,
        data_time=bars[-1].minute,
        fetched_at=FETCHED_AT,
        source="fake",
        status=QuoteStatus.PARTIAL,
        warning="previous close unavailable",
    )

    snapshot = calculate_intraday_strength(
        "run-2",
        quote,
        bars,
        XSHGTradingCalendar(),
        fetched_at=FETCHED_AT,
    )

    assert snapshot.label is StrengthLabel.NEUTRAL
    assert snapshot.confidence is StrengthConfidence.LOW
    assert snapshot.degraded is True
    assert len([component for component in snapshot.components if component.available]) < 4
    volume_ratio = next(
        component for component in snapshot.components if component.name == "minute_volume_ratio"
    )
    assert volume_ratio.available is False
    assert volume_ratio.direction == 0
    assert "25" in volume_ratio.reason


def test_intraday_strength_volume_ratio_zero_baseline_has_explainable_component() -> None:
    bars = minute_series(rising=True, volume_spike=False)[:25]
    bars = [
        bar.model_copy(update={"volume": 0.0, "amount": 0.0})
        if index < 20
        else bar
        for index, bar in enumerate(bars)
    ]
    quote = QuoteSnapshot(
        symbol="600000",
        previous_close=9.9,
        current_price=bars[-1].close,
        data_time=bars[-1].minute,
        fetched_at=FETCHED_AT,
        source="fake",
        status=QuoteStatus.PARTIAL,
        warning="optional fields unavailable",
    )

    snapshot = calculate_intraday_strength(
        "run-zero-volume",
        quote,
        bars,
        XSHGTradingCalendar(),
        fetched_at=FETCHED_AT,
    )

    volume_ratio = next(
        component for component in snapshot.components if component.name == "minute_volume_ratio"
    )
    assert snapshot.minute_volume_ratio is None
    assert volume_ratio.available is False
    assert volume_ratio.direction == 0
    assert "zero" in volume_ratio.reason.lower()


def test_daily_features_mark_insufficient_windows_and_zero_volume_unavailable() -> None:
    bars = [bar.model_copy(update={"volume": 0.0}) for bar in make_daily_bars(10)]

    features = calculate_daily_features(bars)

    assert features.ma5.available is True
    assert features.ma20.available is False
    assert features.return_10.available is False
    assert features.atr14.available is False
    assert features.volume_ratio.available is False


def test_intraday_strength_classifies_weak_and_lowers_confidence_on_low_volume() -> None:
    bars = minute_series(rising=False, volume_spike=False)
    bars = [
        bar.model_copy(update={"volume": 50.0, "amount": bar.close * 50.0})
        if index >= 25
        else bar
        for index, bar in enumerate(bars)
    ]
    quote = QuoteSnapshot(
        symbol="600000",
        previous_close=10.2,
        current_price=bars[-1].close,
        data_time=bars[-1].minute,
        fetched_at=FETCHED_AT,
        source="fake",
        status=QuoteStatus.PARTIAL,
        warning="optional quote fields unavailable",
    )

    snapshot = calculate_intraday_strength(
        "run-weak",
        quote,
        bars,
        XSHGTradingCalendar(),
        fetched_at=FETCHED_AT,
    )

    assert snapshot.label is StrengthLabel.WEAK
    assert snapshot.direction_sum <= -3
    assert snapshot.minute_volume_ratio == pytest.approx(0.5)
    assert snapshot.confidence is StrengthConfidence.LOW


def test_intraday_flat_range_is_neutral_and_position_is_unavailable() -> None:
    bars = minute_series(rising=True, volume_spike=False)
    bars = [
        bar.model_copy(
            update={
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "amount": bar.volume * 10.0,
            }
        )
        for bar in bars
    ]
    quote = QuoteSnapshot(
        symbol="600000",
        previous_close=10.0,
        current_price=10.0,
        data_time=bars[-1].minute,
        fetched_at=FETCHED_AT,
        source="fake",
        status=QuoteStatus.PARTIAL,
        warning="optional quote fields unavailable",
    )

    snapshot = calculate_intraday_strength(
        "run-flat",
        quote,
        bars,
        XSHGTradingCalendar(),
        fetched_at=FETCHED_AT,
    )

    position = next(item for item in snapshot.components if item.name == "intraday_position")
    assert position.available is False
    assert snapshot.label is StrengthLabel.NEUTRAL
    assert snapshot.confidence is StrengthConfidence.LOW
