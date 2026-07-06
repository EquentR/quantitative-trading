from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field


class Settings(BaseModel):
    database_path: Path = Field(default=Path("data/quant_trading.db"))


def load_settings() -> Settings:
    raw_path = os.environ.get("QT_DATABASE_PATH")
    if raw_path:
        return Settings(database_path=Path(raw_path))
    return Settings()
