from pathlib import Path

from quantitative_trading.config import Settings, load_settings


def test_settings_defaults_are_local_and_shanghai_time() -> None:
    settings = Settings()

    assert settings.database_path == Path("data/quant_trading.db")
    assert settings.log_dir == Path("data/logs")
    assert settings.market_provider == "akshare"
    assert settings.intraday_interval_seconds == 180
    assert settings.timezone == "Asia/Shanghai"
    assert settings.enable_market_fetch is True


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
