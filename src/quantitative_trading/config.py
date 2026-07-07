from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QT_")

    database_path: Path = Field(default=Path("data/quant_trading.db"))
    log_dir: Path = Field(default=Path("data/logs"))
    market_provider: str = Field(default="akshare")
    intraday_interval_seconds: int = Field(default=180, ge=1)
    timezone: str = Field(default="Asia/Shanghai")
    enable_market_fetch: bool = Field(default=True)


def load_settings() -> Settings:
    return Settings()
