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
    "QT_API_HOST",
    "QT_API_PORT",
    "QT_API_ACCESS_PASSWORD",
    "QT_API_TOKEN_SECRET",
    "QT_API_TOKEN_TTL_SECONDS",
    "QT_SERVICE_RUN_ON_START_WHEN_SCHEDULER_ENABLED",
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


def test_api_settings_have_safe_defaults(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)

    settings = Settings()

    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 8000
    assert settings.api_access_password is None
    assert settings.api_token_secret is None
    assert settings.api_token_ttl_seconds == 3600
    assert settings.service_run_on_start_when_scheduler_enabled is True


def test_api_settings_can_be_loaded_from_qt_environment(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("QT_API_HOST", "0.0.0.0")
    monkeypatch.setenv("QT_API_PORT", "9000")
    monkeypatch.setenv("QT_API_ACCESS_PASSWORD", "local-dev-password")
    monkeypatch.setenv("QT_API_TOKEN_SECRET", "local-dev-secret")
    monkeypatch.setenv("QT_API_TOKEN_TTL_SECONDS", "120")
    monkeypatch.setenv("QT_SERVICE_RUN_ON_START_WHEN_SCHEDULER_ENABLED", "false")

    settings = Settings()

    assert settings.api_host == "0.0.0.0"
    assert settings.api_port == 9000
    assert settings.api_access_password == "local-dev-password"
    assert settings.api_token_secret == "local-dev-secret"
    assert settings.api_token_ttl_seconds == 120
    assert settings.service_run_on_start_when_scheduler_enabled is False


def test_api_secret_settings_treat_empty_environment_as_unset(monkeypatch) -> None:
    clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("QT_API_ACCESS_PASSWORD", "")
    monkeypatch.setenv("QT_API_TOKEN_SECRET", "")

    settings = Settings()

    assert settings.api_access_password is None
    assert settings.api_token_secret is None


@pytest.mark.parametrize("raw_port", ["0", "65536"])
def test_api_settings_reject_invalid_port_environment(monkeypatch, raw_port: str) -> None:
    clear_runtime_environment(monkeypatch)
    monkeypatch.setenv("QT_API_PORT", raw_port)

    with pytest.raises(ValidationError):
        load_settings()
