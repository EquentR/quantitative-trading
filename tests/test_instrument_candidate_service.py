from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.datasource.miaoxiang import (
    RemoteWatchlistItem,
    RemoteWatchlistResult,
)
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentMetadata,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import InstrumentRepository
from quantitative_trading.instrument.service import (
    InstrumentCandidateService,
    InstrumentSelectionInvalidError,
)
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedSource
from quantitative_trading.watchlist.repository import WatchPinnedRepository


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)


def metadata(
    symbol: str,
    name: str,
    *,
    instrument_type: InstrumentType = InstrumentType.A_SHARE,
    settlement: SettlementCycle = SettlementCycle.T1,
) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        name=name,
        exchange=Exchange.SH,
        instrument_type=instrument_type,
        settlement_cycle=settlement,
        metadata_source="test-directory",
        metadata_checked_at=NOW,
        rule_version="test-rules-v1",
    )


def test_eastmoney_preview_does_not_write_watchlist_and_marks_unknown_unselectable(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "candidate.db")
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog([metadata("600519", "贵州茅台")])
        service = InstrumentCandidateService(connection, now=lambda: NOW)

        preview = service.preview_eastmoney(
            RemoteWatchlistResult(
                items=[
                    RemoteWatchlistItem("600519", "供应商名称", 1),
                    RemoteWatchlistItem("999999", "未知品种", 2),
                ],
                warnings=[],
            )
        )

        assert WatchPinnedRepository(connection).list() == []
        assert preview.expires_at == NOW + timedelta(minutes=10)
        assert preview.items[0].name == "贵州茅台"
        assert preview.items[0].selectable is True
        assert [item.symbol for item in preview.items] == ["600519"]
        assert preview.warnings == [
            "filtered 1 remote instrument outside the supported directory"
        ]


def test_eastmoney_preview_keeps_unknown_disabled_when_directory_is_unavailable(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "candidate-unavailable.db")
    with connect(settings) as connection:
        migrate(connection)
        preview = InstrumentCandidateService(connection, now=lambda: NOW).preview_eastmoney(
            RemoteWatchlistResult(
                items=[RemoteWatchlistItem("999999", "未知品种", 1)],
                warnings=[],
            ),
            directory_available=False,
        )

    assert preview.items[0].instrument_type is InstrumentType.UNKNOWN
    assert preview.items[0].selectable is False


def test_search_preview_uses_catalog_and_does_not_write_watchlist(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "search.db")
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog(
            [metadata("600519", "贵州茅台"), metadata("600000", "浦发银行")]
        )
        service = InstrumentCandidateService(connection, now=lambda: NOW)

        preview = service.search(" 茅台 ")

        assert preview.query == "茅台"
        assert [item.symbol for item in preview.items] == ["600519"]
        assert WatchPinnedRepository(connection).list() == []


def test_selection_is_atomic_defaults_verified_items_and_preserves_existing_fields(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "select.db")
    with connect(settings) as connection:
        migrate(connection)
        instruments = InstrumentRepository(connection)
        instruments.replace_catalog(
            [metadata("600519", "贵州茅台"), metadata("600000", "浦发银行")]
        )
        watchlist = WatchPinnedRepository(connection)
        watchlist.upsert(
            WatchPinnedInput(
                symbol="600000",
                name="原名称",
                rank=7,
                plan_enabled=False,
                note="保留备注",
            ),
            source=WatchPinnedSource.MANUAL,
            now=NOW,
        )
        service = InstrumentCandidateService(connection, now=lambda: NOW)
        preview = service.preview_eastmoney(
            RemoteWatchlistResult(
                items=[
                    RemoteWatchlistItem("600519", "贵州茅台", 1),
                    RemoteWatchlistItem("600000", "浦发银行", 2),
                ],
                warnings=[],
            )
        )

        selected = service.select(preview.preview_id, ["600519", "600000"])
        repeated = service.select(preview.preview_id, ["600519", "600000"])

        by_symbol = {item.symbol: item for item in watchlist.list()}
        assert selected.items == repeated.items
        assert by_symbol["600519"].rank == 1
        assert by_symbol["600519"].plan_enabled is True
        assert by_symbol["600519"].source is WatchPinnedSource.SYNCED
        assert by_symbol["600000"].rank == 7
        assert by_symbol["600000"].plan_enabled is False
        assert by_symbol["600000"].note == "保留备注"
        assert by_symbol["600000"].source is WatchPinnedSource.MANUAL_SYNCED


def test_search_confirmation_merges_existing_synced_source(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "search-source-merge.db")
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog(
            [metadata("600519", "贵州茅台")]
        )
        watchlist = WatchPinnedRepository(connection)
        watchlist.upsert(
            WatchPinnedInput(
                symbol="600519",
                name="贵州茅台",
                rank=1,
                plan_enabled=True,
            ),
            source=WatchPinnedSource.SYNCED,
            now=NOW,
        )
        service = InstrumentCandidateService(connection, now=lambda: NOW)

        preview = service.search("600519")
        result = service.select(preview.preview_id, ["600519"])

        assert result.items[0].source is WatchPinnedSource.MANUAL_SYNCED


def test_unknown_settlement_etf_is_added_watch_only_and_unknown_type_rejects_batch(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "safe-select.db")
    with connect(settings) as connection:
        migrate(connection)
        InstrumentRepository(connection).replace_catalog(
            [
                metadata(
                    "510300",
                    "规则待确认ETF",
                    instrument_type=InstrumentType.ETF,
                    settlement=SettlementCycle.UNKNOWN,
                )
            ]
        )
        service = InstrumentCandidateService(connection, now=lambda: NOW)
        preview = service.preview_eastmoney(
            RemoteWatchlistResult(
                items=[
                    RemoteWatchlistItem("510300", "规则待确认ETF", 1),
                    RemoteWatchlistItem("999999", "未知", 2),
                ],
                warnings=[],
            )
        )

        with pytest.raises(InstrumentSelectionInvalidError):
            service.select(preview.preview_id, ["510300", "999999"])
        assert WatchPinnedRepository(connection).list() == []

        selected = service.select(preview.preview_id, ["510300"])
        assert selected.items[0].plan_enabled is False
        assert selected.warnings == [
            "510300 settlement cycle is unknown; plan remains disabled"
        ]


def test_selection_rejects_preview_metadata_invalidated_by_current_catalog(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "invalidated-preview.db")
    with connect(settings) as connection:
        migrate(connection)
        instruments = InstrumentRepository(connection)
        instruments.replace_catalog(
            [
                metadata(
                    "510300",
                    "沪深300ETF",
                    instrument_type=InstrumentType.ETF,
                )
            ]
        )
        service = InstrumentCandidateService(connection, now=lambda: NOW)
        preview = service.search("510300")
        conflicted = InstrumentMetadata(
            symbol="510300",
            name="沪深300ETF",
            exchange=None,
            instrument_type=InstrumentType.UNKNOWN,
            settlement_cycle=SettlementCycle.UNKNOWN,
            metadata_source="directory_conflict",
            metadata_checked_at=NOW + timedelta(minutes=1),
            rule_version="unverified-v1",
            warnings=["instrument metadata conflict"],
        )
        instruments.replace_catalog([conflicted])

        with pytest.raises(InstrumentSelectionInvalidError):
            service.select(preview.preview_id, ["510300"])

        assert instruments.get("510300") == conflicted
        assert WatchPinnedRepository(connection).list() == []
