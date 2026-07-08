from datetime import UTC, datetime

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedSource
from quantitative_trading.watchlist.repository import WatchPinnedRepository


def test_watch_pinned_crud_and_merge_preserves_local_switch(tmp_path) -> None:
    now = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = WatchPinnedRepository(connection)
        repository.replace_all(
            [
                WatchPinnedInput(
                    symbol="600000",
                    name="浦发银行",
                    rank=10,
                    plan_enabled=True,
                    note="保留开关",
                )
            ],
            source=WatchPinnedSource.MANUAL,
            now=now,
        )

        merged = repository.merge_synced(
            [
                WatchPinnedInput(
                    symbol="600000",
                    name="浦发银行新名",
                    rank=1,
                    plan_enabled=False,
                    note="同步备注不应覆盖",
                ),
                WatchPinnedInput(
                    symbol="000001",
                    name="平安银行",
                    rank=2,
                    plan_enabled=True,
                    note="同步新股默认关",
                ),
            ],
            now=now,
        )

    by_symbol = {item.symbol: item for item in merged}
    assert by_symbol["600000"].name == "浦发银行新名"
    assert by_symbol["600000"].rank == 1
    assert by_symbol["600000"].plan_enabled is True
    assert by_symbol["600000"].note == "保留开关"
    assert by_symbol["600000"].source is WatchPinnedSource.MANUAL_SYNCED
    assert by_symbol["000001"].plan_enabled is False
    assert by_symbol["000001"].source is WatchPinnedSource.SYNCED


def test_merge_synced_deletes_missing_synced_rows_and_keeps_manual_synced_rows(
    tmp_path,
) -> None:
    now = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = WatchPinnedRepository(connection)
        repository.upsert(
            WatchPinnedInput(
                symbol="600000",
                name="浦发银行",
                rank=1,
                plan_enabled=False,
                note="",
            ),
            source=WatchPinnedSource.SYNCED,
            now=now,
        )
        repository.upsert(
            WatchPinnedInput(
                symbol="000001",
                name="平安银行",
                rank=2,
                plan_enabled=True,
                note="保留本地备注",
            ),
            source=WatchPinnedSource.MANUAL_SYNCED,
            now=now,
        )

        merged = repository.merge_synced(
            [
                WatchPinnedInput(
                    symbol="600519",
                    name="贵州茅台",
                    rank=1,
                    plan_enabled=True,
                    note="同步备注不应写入",
                )
            ],
            now=now,
        )

    by_symbol = {item.symbol: item for item in merged}
    assert "600000" not in by_symbol
    assert by_symbol["000001"].source is WatchPinnedSource.MANUAL
    assert by_symbol["000001"].plan_enabled is True
    assert by_symbol["000001"].note == "保留本地备注"
    assert by_symbol["600519"].source is WatchPinnedSource.SYNCED
    assert by_symbol["600519"].plan_enabled is False
    assert by_symbol["600519"].note == ""


def test_replace_all_rejects_duplicate_symbols(tmp_path) -> None:
    now = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = WatchPinnedRepository(connection)

        with pytest.raises(ValueError, match="duplicate symbol 600000"):
            repository.replace_all(
                [
                    WatchPinnedInput(symbol="600000", name="浦发银行", rank=1),
                    WatchPinnedInput(symbol="600000", name="浦发银行", rank=2),
                ],
                source=WatchPinnedSource.MANUAL,
                now=now,
            )


def test_merge_synced_rejects_duplicate_symbols(tmp_path) -> None:
    now = datetime(2026, 7, 8, 10, 0, tzinfo=UTC)
    settings = Settings(database_path=tmp_path / "app.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = WatchPinnedRepository(connection)

        with pytest.raises(ValueError, match="duplicate symbol 600000"):
            repository.merge_synced(
                [
                    WatchPinnedInput(symbol="600000", name="浦发银行", rank=1),
                    WatchPinnedInput(symbol="600000", name="浦发银行", rank=2),
                ],
                now=now,
            )
