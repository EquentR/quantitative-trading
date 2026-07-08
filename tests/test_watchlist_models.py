from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedItem


def test_watch_pinned_input_accepts_plan_switch() -> None:
    item = WatchPinnedInput(
        symbol="600000",
        name="浦发银行",
        rank=1,
        plan_enabled=False,
        note="观察",
    )

    assert item.symbol == "600000"
    assert item.plan_enabled is False


def test_watch_pinned_input_rejects_invalid_symbol() -> None:
    with pytest.raises(ValidationError):
        WatchPinnedInput(symbol="BAD", name="错误", rank=1)


def test_watch_pinned_item_requires_timezone_updated_at() -> None:
    with pytest.raises(ValidationError):
        WatchPinnedItem(
            symbol="600000",
            name="浦发银行",
            rank=1,
            plan_enabled=False,
            source="manual",
            note="",
            updated_at=datetime(2026, 7, 8, 10, 0),
        )

    item = WatchPinnedItem(
        symbol="600000",
        name="浦发银行",
        rank=1,
        plan_enabled=False,
        source="manual",
        note="",
        updated_at=datetime(2026, 7, 8, 10, 0, tzinfo=UTC),
    )
    assert item.source == "manual"
