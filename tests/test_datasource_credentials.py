from datetime import UTC, datetime

from quantitative_trading.datasource.credentials import redact_secret
from quantitative_trading.datasource.status import DatasourceCredentialsRepository
from quantitative_trading.config import Settings
from quantitative_trading.storage.sqlite import connect, migrate


def test_redact_secret_never_returns_key() -> None:
    assert redact_secret("abcdef123456") == "configured"
    assert "abcdef" not in redact_secret("abcdef123456")


def test_blank_secret_is_missing() -> None:
    assert redact_secret("") == "missing"


def test_none_secret_is_missing() -> None:
    assert redact_secret(None) == "missing"


def test_repository_uses_neutral_internal_secret_field(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "credentials.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = DatasourceCredentialsRepository(connection)

        credential = repository.save_secret(
            "eastmoney",
            "secret-eastmoney-key",
            now=datetime(2026, 7, 9, 2, 0, tzinfo=UTC),
        )

    assert credential.stored_secret == "secret-eastmoney-key"
    assert not hasattr(credential, "encrypted_secret")
    assert "secret-eastmoney-key" not in repr(credential)
