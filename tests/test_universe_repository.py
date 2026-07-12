from datetime import UTC, datetime

from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.universe.models import (
    UniverseSnapshot,
    UniverseSnapshotStatus,
)
from quantitative_trading.universe.repository import UniverseSnapshotRepository


CREATED_AT = datetime(2026, 7, 12, 6, 0, tzinfo=UTC)


def universe_snapshot() -> UniverseSnapshot:
    return UniverseSnapshot(
        created_at=CREATED_AT,
        status=UniverseSnapshotStatus.OK,
        warnings=[],
        members=[],
    )


def test_save_without_commit_can_be_rolled_back_and_is_not_visible_elsewhere(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "universe.db")
    snapshot = universe_snapshot()

    with connect(settings) as writer:
        migrate(writer)
        snapshot_id = UniverseSnapshotRepository(writer).save(snapshot, commit=False)

        with connect(settings) as reader:
            assert UniverseSnapshotRepository(reader).get(snapshot_id) is None

        writer.rollback()

    with connect(settings) as reader:
        assert UniverseSnapshotRepository(reader).get(snapshot_id) is None


def test_save_commits_by_default_and_is_visible_from_new_connection(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "universe.db")
    snapshot = universe_snapshot()

    with connect(settings) as writer:
        migrate(writer)
        snapshot_id = UniverseSnapshotRepository(writer).save(snapshot)

    with connect(settings) as reader:
        assert UniverseSnapshotRepository(reader).get(snapshot_id) == snapshot
