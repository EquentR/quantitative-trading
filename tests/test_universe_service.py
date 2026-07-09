from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from quantitative_trading.ledger.models import Position
from quantitative_trading.universe.models import UniverseSource
from quantitative_trading.universe.service import build_universe
from quantitative_trading.watchlist.models import WatchPinnedItem, WatchPinnedSource


CREATED_AT = datetime(2026, 7, 8, 1, 30, tzinfo=UTC)
LEDGER_UPDATED_AT = datetime(2026, 7, 8, 1, 0, tzinfo=UTC)
WATCH_UPDATED_AT = datetime(2026, 7, 8, 1, 15, tzinfo=UTC)


def position(symbol: str = "600000", name: str = "持仓名称") -> Position:
    return Position(
        symbol=symbol,
        name=name,
        quantity=1000,
        available_quantity=800,
        cost_price=9.5,
        opened_at=date(2026, 7, 6),
        note="manual ledger",
        updated_at=LEDGER_UPDATED_AT,
    )


def watch_item(
    symbol: str = "600000",
    *,
    name: str = "观察名称",
    rank: int = 1,
    plan_enabled: bool = False,
) -> WatchPinnedItem:
    return WatchPinnedItem(
        symbol=symbol,
        name=name,
        rank=rank,
        plan_enabled=plan_enabled,
        source=WatchPinnedSource.MANUAL,
        note="watch",
        updated_at=WATCH_UPDATED_AT,
    )


def test_holding_overrides_disabled_watchlist_and_keeps_both_sources() -> None:
    members = build_universe(
        positions=[position()],
        watchlist=[watch_item(plan_enabled=False)],
        created_at=CREATED_AT,
    )

    assert len(members) == 1
    member = members[0]
    assert member.symbol == "600000"
    assert member.name == "持仓名称"
    assert set(member.sources) == {UniverseSource.HOLDING, UniverseSource.WATCH_PINNED}
    assert member.priority == 0
    assert member.ledger_updated_at == LEDGER_UPDATED_AT
    assert member.watch_pinned_rank == 1
    assert member.plan_enabled is True
    assert member.plan_enabled_source == UniverseSource.HOLDING
    assert member.created_at == CREATED_AT


def test_disabled_watchlist_only_member_stays_visible_but_not_plan_enabled() -> None:
    members = build_universe(
        positions=[],
        watchlist=[watch_item(plan_enabled=False)],
        created_at=CREATED_AT,
    )

    assert len(members) == 1
    assert members[0].sources == [UniverseSource.WATCH_PINNED]
    assert members[0].plan_enabled is False
    assert members[0].plan_enabled_source == UniverseSource.WATCH_PINNED
    assert members[0].ledger_updated_at is None
    assert members[0].watch_pinned_rank == 1


def test_watchlist_only_member_uses_watchlist_plan_enabled_value() -> None:
    members = build_universe(
        positions=[],
        watchlist=[watch_item(plan_enabled=True)],
        created_at=CREATED_AT,
    )

    assert members[0].plan_enabled is True
    assert members[0].plan_enabled_source == UniverseSource.WATCH_PINNED


def test_universe_order_is_deterministic_with_holdings_before_watchlist_rank() -> None:
    members = build_universe(
        positions=[position("600010"), position("000001")],
        watchlist=[
            watch_item("600000", rank=2, plan_enabled=True),
            watch_item("000002", rank=1, plan_enabled=True),
        ],
        created_at=CREATED_AT,
    )

    assert [member.symbol for member in members] == [
        "000001",
        "600010",
        "000002",
        "600000",
    ]
    assert [member.priority for member in members] == [0, 0, 1, 2]


def test_universe_rejects_naive_created_at() -> None:
    with pytest.raises(ValidationError):
        build_universe(
            positions=[position()],
            watchlist=[],
            created_at=datetime(2026, 7, 8, 1, 30),
        )
