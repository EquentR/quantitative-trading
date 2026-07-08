from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedSource
from quantitative_trading.watchlist.repository import WatchPinnedRepository
from quantitative_trading.watchlist.service import (
    ReadOnlyWatchPinnedService,
    WatchPinnedService,
)


def make_services(tmp_path) -> tuple[WatchPinnedService, ReadOnlyWatchPinnedService, object]:
    settings = Settings(database_path=tmp_path / "watchlist.db")
    connection_cm = connect(settings)
    connection = connection_cm.__enter__()
    migrate(connection)
    repository = WatchPinnedRepository(connection)
    return (
        WatchPinnedService(repository),
        ReadOnlyWatchPinnedService(repository),
        connection_cm,
    )


def valid_input(symbol: str = "600000") -> WatchPinnedInput:
    return WatchPinnedInput(
        symbol=symbol,
        name="浦发银行",
        rank=1,
        plan_enabled=True,
        note="观察",
    )


def test_watch_pinned_service_upserts_with_explicit_time(tmp_path) -> None:
    service, _, connection_cm = make_services(tmp_path)
    try:
        now = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)

        item = service.upsert_pinned(
            valid_input(),
            source=WatchPinnedSource.MANUAL,
            now=now,
        )

        assert item.updated_at == now
        assert service.get_pinned("600000") is not None
    finally:
        connection_cm.__exit__(None, None, None)


def test_read_only_watch_pinned_service_can_list_pinned(tmp_path) -> None:
    service, read_only, connection_cm = make_services(tmp_path)
    try:
        service.upsert_pinned(
            valid_input(),
            source=WatchPinnedSource.MANUAL,
            now=datetime(2026, 7, 8, 10, 0, tzinfo=UTC),
        )

        items = read_only.list_pinned()

        assert [item.symbol for item in items] == ["600000"]
    finally:
        connection_cm.__exit__(None, None, None)


def test_watch_pinned_service_import_csv_rejects_duplicates_and_accepts_bom(
    tmp_path,
) -> None:
    service, _, connection_cm = make_services(tmp_path)
    csv_path = tmp_path / "watchlist.csv"
    csv_path.write_text(
        "\ufeffsymbol,name,rank,plan_enabled,note\n"
        "600000,浦发银行,1,true,观察\n"
        "600000,浦发银行,2,false,重复\n",
        encoding="utf-8",
    )

    try:
        with pytest.raises(ValueError, match="duplicate symbol 600000"):
            service.import_csv(csv_path, source=WatchPinnedSource.MANUAL)
    finally:
        connection_cm.__exit__(None, None, None)
