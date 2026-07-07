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
    api_host: str = Field(default="127.0.0.1")
    api_port: int = Field(default=8000, ge=1, le=65535)
    # API 认证只读取本地配置或环境变量，避免把真实密钥写入代码库。
    api_access_password: str | None = Field(default=None)
    api_token_secret: str | None = Field(default=None)
    api_token_ttl_seconds: int = Field(default=3600, ge=60)
    service_run_on_start_when_scheduler_enabled: bool = Field(default=True)


def load_settings() -> Settings:
    return Settings()
