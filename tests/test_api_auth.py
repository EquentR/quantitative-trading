from datetime import UTC, datetime

from quantitative_trading.config import Settings
from quantitative_trading.storage.api_auth import ApiAuthRepository
from quantitative_trading.storage.sqlite import connect, migrate


NOW = datetime(2026, 7, 7, 2, 0, tzinfo=UTC)


def test_auth_repository_starts_unconfigured(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)

        state = repository.get()

    assert state.password_hash is None
    assert state.token_secret != ""
    assert state.updated_at.tzinfo is not None


def test_auth_repository_get_reuses_generated_token_secret(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)

        initial = repository.get()
        loaded = repository.get()

    assert loaded.token_secret == initial.token_secret


def test_auth_repository_saves_password_hash_without_losing_token_secret(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)
        initial = repository.get()

        saved = repository.save_password_hash("hash-value", now=NOW)

    assert saved.password_hash == "hash-value"
    assert saved.token_secret == initial.token_secret
    assert saved.updated_at == NOW


def test_auth_repository_saves_token_secret_without_losing_password_hash(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)
        repository.save_password_hash("hash-value", now=NOW)

        saved = repository.save_token_secret("rotated-secret", now=NOW)

    assert saved.password_hash == "hash-value"
    assert saved.token_secret == "rotated-secret"
    assert saved.updated_at == NOW


def test_auth_state_repr_masks_password_hash_and_token_secret(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "auth.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = ApiAuthRepository(connection)

        state = repository.save_password_hash("hash-value", now=NOW)

    representation = repr(state)
    assert "hash-value" not in representation
    assert state.token_secret not in representation
