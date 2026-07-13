from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QT_")

    database_path: Path = Field(default=Path("data/quant_trading.db"))
    log_dir: Path = Field(default=Path("data/logs"))
    market_provider: str = Field(default="akshare")
    intraday_interval_seconds: int = Field(default=180, ge=1)
    market_stale_trading_minutes: int = Field(default=6, ge=1, le=60)
    timezone: str = Field(default="Asia/Shanghai")
    enable_market_fetch: bool = Field(default=True)
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000, ge=1, le=65535)
    # API 认证只读取本地配置或环境变量，避免把真实密钥写入代码库。
    api_access_password: str | None = Field(default=None)
    api_token_secret: str | None = Field(default=None)
    api_token_ttl_seconds: int = Field(default=3600, ge=60)
    # 调度恢复后是否立即跑一次快照，避免重启后长时间等到下一个轮询周期。
    service_run_on_start_when_scheduler_enabled: bool = Field(default=True)

    @field_validator("api_access_password", "api_token_secret", mode="before")
    @classmethod
    def _blank_secret_to_none(cls, value: object) -> object:
        # .env.example 使用空占位符；空字符串应视为未配置，不能变成空密码。
        if isinstance(value, str) and value.strip() == "":
            return None
        return value


def load_settings() -> Settings:
    return Settings()
