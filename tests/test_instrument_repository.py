from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from quantitative_trading.config import Settings
from quantitative_trading.instrument.models import (
    Exchange,
    InstrumentCandidate,
    InstrumentMetadata,
    InstrumentPreview,
    InstrumentPreviewSource,
    InstrumentType,
    SettlementCycle,
)
from quantitative_trading.instrument.repository import (
    InstrumentCatalogState,
    InstrumentCatalogStateRepository,
    InstrumentPreviewExpiredError,
    InstrumentPreviewNotFoundError,
    InstrumentPreviewRepository,
    InstrumentRepository,
)
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.watchlist.models import WatchPinnedInput, WatchPinnedSource
from quantitative_trading.watchlist.repository import WatchPinnedRepository


NOW = datetime(2026, 7, 15, 2, 0, tzinfo=UTC)


def metadata(
    symbol: str,
    name: str,
    *,
    exchange: Exchange = Exchange.SH,
    instrument_type: InstrumentType = InstrumentType.A_SHARE,
    settlement_cycle: SettlementCycle = SettlementCycle.T1,
    source: str = "akshare_sh_a_share",
    listing_date: date | None = None,
) -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol=symbol,
        name=name,
        exchange=exchange,
        instrument_type=instrument_type,
        settlement_cycle=settlement_cycle,
        listing_date=listing_date,
        metadata_source=source,
        metadata_checked_at=NOW,
        rule_version="instrument-rules-v1",
    )


def test_catalog_round_trips_verified_listing_date(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "catalog-listing-date.db")
    expected = date(2001, 8, 27)
    with connect(settings) as connection:
        migrate(connection)
        repository = InstrumentRepository(connection)
        repository.replace_catalog(
            [metadata("600000", "浦发银行", listing_date=expected)]
        )

        loaded = repository.get("600000")

    assert loaded is not None
    assert loaded.listing_date == expected


def test_migrate_adds_nullable_listing_date_to_existing_catalog(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "legacy-catalog-listing-date.db")
    with connect(settings) as connection:
        connection.executescript(
            """
            CREATE TABLE instruments (
              symbol TEXT PRIMARY KEY NOT NULL,
              name TEXT NOT NULL,
              exchange TEXT,
              instrument_type TEXT NOT NULL,
              settlement_cycle TEXT NOT NULL,
              price_limit_ratio REAL,
              metadata_source TEXT NOT NULL,
              metadata_checked_at TEXT NOT NULL,
              rule_version TEXT NOT NULL,
              is_active INTEGER NOT NULL,
              warnings_json TEXT NOT NULL
            );
            INSERT INTO instruments VALUES (
              '600000', 'legacy', 'SH', 'a_share', 't1', 0.1,
              'legacy-directory', '2026-07-15T02:00:00+00:00',
              'legacy-rules-v1', 1, '[]'
            );
            """
        )

        migrate(connection)

        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(instruments)")
        }
        row = connection.execute(
            "SELECT name, listing_date FROM instruments WHERE symbol='600000'"
        ).fetchone()

    assert "listing_date" in columns
    assert row["name"] == "legacy"
    assert row["listing_date"] is None


def test_replace_catalog_marks_missing_rows_inactive_and_keeps_other_metadata(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "catalog.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = InstrumentRepository(connection)
        repository.replace_catalog(
            [metadata("600000", "first"), metadata("600519", "second")]
        )

        repository.replace_catalog([metadata("600519", "renamed")])

        assert repository.get("600000") is None
        inactive = repository.get("600000", include_inactive=True)
        active = repository.get("600519")

    assert inactive is not None
    assert inactive.name == "first"
    assert active is not None
    assert active.name == "renamed"


def test_search_orders_exact_code_then_prefix_then_name_and_caps_limit(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "search.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = InstrumentRepository(connection)
        repository.replace_catalog(
            [
                metadata("600519", "贵州茅台"),
                metadata("600000", "茅台银行"),
                metadata("000001", "平安茅台"),
            ]
        )

        by_code = repository.search("600519", limit=2)
        by_prefix = repository.search("600", limit=2)
        by_name = repository.search("茅台", limit=2)

    assert [item.symbol for item in by_code] == ["600519"]
    assert [item.symbol for item in by_prefix] == ["600000", "600519"]
    assert [item.symbol for item in by_name] == ["600000", "000001"]


def test_search_treats_sql_like_characters_as_literal_text(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "literal-search.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = InstrumentRepository(connection)
        repository.replace_catalog(
            [metadata("600519", "name%literal"), metadata("600000", "ordinary")]
        )

        result = repository.search("%", limit=50)

    assert [item.symbol for item in result] == ["600519"]


def test_search_prioritizes_exact_name_then_name_prefix_then_contains(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "name-order.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = InstrumentRepository(connection)
        repository.replace_catalog(
            [
                metadata("600003", "银行观察"),
                metadata("600001", "银行"),
                metadata("600002", "平安银行"),
            ]
        )

        result = repository.search("银行")

    assert [item.symbol for item in result] == ["600001", "600003", "600002"]


def test_catalog_refresh_disables_watch_plan_when_metadata_is_no_longer_active(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "reconcile-watch.db")
    with connect(settings) as connection:
        migrate(connection)
        instruments = InstrumentRepository(connection)
        watchlist = WatchPinnedRepository(connection)
        instruments.replace_catalog([metadata("600519", "贵州茅台")])
        watchlist.upsert(
            WatchPinnedInput(
                symbol="600519",
                name="贵州茅台",
                rank=1,
                plan_enabled=True,
            ),
            source=WatchPinnedSource.MANUAL,
            now=NOW,
        )

        instruments.replace_catalog([])

        item = watchlist.get("600519")
    assert item is not None
    assert item.instrument_type is InstrumentType.UNKNOWN
    assert item.plan_enabled is False


def test_catalog_state_round_trip_preserves_safe_failure_state(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "state.db")
    state = InstrumentCatalogState(
        source="akshare_sh_a_share",
        last_attempt_at=NOW,
        last_success_at=NOW - timedelta(days=1),
        data_trade_date=date(2026, 7, 14),
        status="stale",
        last_error="provider unavailable",
        warnings=["partial directory warning"],
        updated_at=NOW,
    )
    with connect(settings) as connection:
        migrate(connection)
        repository = InstrumentCatalogStateRepository(connection)
        repository.save(state)

        loaded = repository.get(state.source)

    assert loaded == state


def test_catalog_state_repository_sanitizes_error_and_warning_text(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "safe-state.db")
    secret = "synthetic-secret-value"
    with connect(settings) as connection:
        migrate(connection)
        loaded = InstrumentCatalogStateRepository(connection).save(
            InstrumentCatalogState(
                source="test-source",
                last_attempt_at=NOW,
                status="failed",
                last_error=f"api_key={secret} /tmp/private.db",
                warnings=[f"token={secret} /tmp/private.json"],
                updated_at=NOW,
            )
        )

    assert secret not in loaded.last_error
    assert secret not in loaded.warnings[0]


def test_preview_repository_distinguishes_missing_and_expired_and_deletes_expired(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "preview.db")
    preview_id = uuid4()
    candidate = InstrumentCandidate.from_metadata(
        metadata("600519", "贵州茅台"),
        source=InstrumentPreviewSource.INSTRUMENT_SEARCH,
        source_rank=None,
        already_monitored=False,
        selectable=True,
    )
    preview = InstrumentPreview(
        preview_id=preview_id,
        source=InstrumentPreviewSource.INSTRUMENT_SEARCH,
        query="茅台",
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
        items=[candidate],
    )
    with connect(settings) as connection:
        migrate(connection)
        repository = InstrumentPreviewRepository(connection)
        repository.save(preview)

        assert repository.get(preview_id, now=NOW) == preview
        with pytest.raises(InstrumentPreviewExpiredError):
            repository.get(preview_id, now=preview.expires_at)
        with pytest.raises(InstrumentPreviewNotFoundError):
            repository.get(preview_id, now=NOW)
        with pytest.raises(InstrumentPreviewNotFoundError):
            repository.get(UUID("00000000-0000-4000-8000-000000000001"), now=NOW)
