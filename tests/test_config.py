from pathlib import Path

import pytest
from pydantic import ValidationError

from quantitative_trading.config import Settings, load_settings


RUNTIME_ENV_NAMES = (
    "QT_DATABASE_PATH",
    "QT_LOG_DIR",
    "QT_MARKET_PROVIDER",
    "QT_INTRADAY_INTERVAL_SECONDS",
    "QT_TIMEZONE",
    "QT_ENABLE_MARKET_FETCH",
)


def clear_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in RUNTIME_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_settings_defaults_are_local_and_shanghai_time(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)

    settings = Settings()

    assert settings.database_path == Path("data/quant_trading.db")
    assert settings.log_dir == Path("data/logs")
    assert settings.market_provider == "akshare"
    assert settings.intraday_interval_seconds == 180
    assert settings.timezone == "Asia/Shanghai"
    assert settings.enable_market_fetch is True


def test_load_settings_without_environment_returns_defaults(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)

    settings = load_settings()

    assert settings == Settings()


def test_load_settings_reads_runtime_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("QT_DATABASE_PATH", str(tmp_path / "account.db"))
    monkeypatch.setenv("QT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("QT_MARKET_PROVIDER", "fake")
    monkeypatch.setenv("QT_INTRADAY_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("QT_TIMEZONE", "Asia/Shanghai")
    monkeypatch.setenv("QT_ENABLE_MARKET_FETCH", "false")

    settings = load_settings()

    assert settings.database_path == tmp_path / "account.db"
    assert settings.log_dir == tmp_path / "logs"
    assert settings.market_provider == "fake"
    assert settings.intraday_interval_seconds == 60
    assert settings.timezone == "Asia/Shanghai"
    assert settings.enable_market_fetch is False


def test_load_settings_reads_non_default_timezone(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("QT_TIMEZONE", "Asia/Singapore")

    settings = load_settings()

    assert settings.timezone == "Asia/Singapore"


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("false", False),
        ("true", True),
        ("0", False),
        ("1", True),
    ],
)
def test_load_settings_parses_valid_boolean_spellings(
    monkeypatch,
    raw_value: str,
    expected: bool,
) -> None:
    clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("QT_ENABLE_MARKET_FETCH", raw_value)

    settings = load_settings()

    assert settings.enable_market_fetch is expected


def test_load_settings_rejects_invalid_boolean_environment(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("QT_ENABLE_MARKET_FETCH", "not-a-bool")

    with pytest.raises(ValidationError):
        load_settings()


def test_load_settings_rejects_invalid_interval_environment(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("QT_INTRADAY_INTERVAL_SECONDS", "not-an-int")

    with pytest.raises(ValidationError):
        load_settings()


def test_load_settings_rejects_non_positive_interval_environment(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("QT_INTRADAY_INTERVAL_SECONDS", "0")

    with pytest.raises(ValidationError):
        load_settings()
