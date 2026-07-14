from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QT_")

    database_path: Path = Field(default=Path("data/quant_trading.db"))
    log_dir: Path = Field(default=Path("data/logs"))
    market_provider: str = Field(default="akshare")
    intraday_interval_seconds: int = Field(default=180, ge=1)
    market_stale_trading_minutes: int = Field(default=6, ge=1, le=60)
    market_strength_rule_version: str = Field(default="intraday-strength-v1", min_length=1)
    market_strength_previous_close_pct: float = Field(default=0.5, gt=0)
    market_strength_open_pct: float = Field(default=0.3, gt=0)
    market_strength_vwap_pct: float = Field(default=0.2, gt=0)
    market_strength_momentum_5_pct: float = Field(default=0.3, gt=0)
    market_strength_momentum_15_pct: float = Field(default=0.6, gt=0)
    market_strength_position_high: float = Field(default=0.70, ge=0, le=1)
    market_strength_position_low: float = Field(default=0.30, ge=0, le=1)
    market_strength_volume_high: float = Field(default=1.5, gt=0)
    market_strength_volume_low: float = Field(default=0.8, ge=0)
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
    email_retry_delays_minutes: tuple[int, ...] = Field(
        default=(1, 5, 15, 30, 60), min_length=1
    )
    email_lease_seconds: int = Field(default=60, ge=1)

    @field_validator("api_access_password", "api_token_secret", mode="before")
    @classmethod
    def _blank_secret_to_none(cls, value: object) -> object:
        # .env.example 使用空占位符；空字符串应视为未配置，不能变成空密码。
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    @model_validator(mode="after")
    def strength_threshold_ranges_must_be_ordered(self) -> "Settings":
        if self.market_strength_position_high <= self.market_strength_position_low:
            raise ValueError("strength position high must exceed position low")
        if self.market_strength_volume_high <= self.market_strength_volume_low:
            raise ValueError("strength volume high must exceed volume low")
        if any(delay < 0 for delay in self.email_retry_delays_minutes):
            raise ValueError("email retry delays must be non-negative")
        return self


def load_settings() -> Settings:
    return Settings()
