from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    database_path: Path = Field(default=Path("data/quant_trading.db"))
    log_dir: Path = Field(default=Path("data/logs"))
    market_provider: str = Field(default="akshare")
    intraday_interval_seconds: int = Field(default=180, ge=1)
    timezone: str = Field(default="Asia/Shanghai")
    enable_market_fetch: bool = Field(default=True)


def load_settings() -> Settings:
    return Settings(
        database_path=Path(os.environ.get("QT_DATABASE_PATH", "data/quant_trading.db")),
        log_dir=Path(os.environ.get("QT_LOG_DIR", "data/logs")),
        market_provider=os.environ.get("QT_MARKET_PROVIDER", "akshare"),
        intraday_interval_seconds=int(os.environ.get("QT_INTRADAY_INTERVAL_SECONDS", "180")),
        timezone=os.environ.get("QT_TIMEZONE", "Asia/Shanghai"),
        enable_market_fetch=_env_bool("QT_ENABLE_MARKET_FETCH", True),
    )
