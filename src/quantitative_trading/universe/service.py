from __future__ import annotations

from datetime import datetime

from quantitative_trading.ledger.models import Position
from quantitative_trading.instrument.models import InstrumentMetadata
from quantitative_trading.universe.models import UniverseMember, UniverseSource
from quantitative_trading.watchlist.models import WatchPinnedItem


def build_universe(
    *,
    positions: list[Position],
    watchlist: list[WatchPinnedItem],
    instrument_metadata: dict[str, InstrumentMetadata] | None = None,
    created_at: datetime,
) -> list[UniverseMember]:
    members_by_symbol: dict[str, UniverseMember] = {}
    metadata_by_symbol = instrument_metadata or {}

    watch_by_symbol = {item.symbol: item for item in watchlist}
    for position in positions:
        watch_item = watch_by_symbol.get(position.symbol)
        sources = [UniverseSource.HOLDING]
        if watch_item is not None:
            sources.append(UniverseSource.WATCH_PINNED)

        members_by_symbol[position.symbol] = UniverseMember(
            symbol=position.symbol,
            name=position.name,
            instrument=metadata_by_symbol.get(position.symbol),
            sources=sources,
            priority=0,
            ledger_updated_at=position.updated_at,
            watch_pinned_rank=watch_item.rank if watch_item is not None else None,
            plan_enabled=True,
            plan_enabled_source=UniverseSource.HOLDING,
            created_at=created_at,
        )

    for item in watchlist:
        if item.symbol in members_by_symbol:
            continue
        members_by_symbol[item.symbol] = UniverseMember(
            symbol=item.symbol,
            name=item.name,
            instrument=metadata_by_symbol.get(item.symbol),
            sources=[UniverseSource.WATCH_PINNED],
            priority=item.rank,
            ledger_updated_at=None,
            watch_pinned_rank=item.rank,
            plan_enabled=item.plan_enabled,
            plan_enabled_source=UniverseSource.WATCH_PINNED,
            created_at=created_at,
        )

    return sorted(members_by_symbol.values(), key=lambda member: (member.priority, member.symbol))
