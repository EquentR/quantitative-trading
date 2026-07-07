from __future__ import annotations

import json
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from quantitative_trading.account.models import AccountSnapshot
from quantitative_trading.account.service import AccountService


class DebugServiceRunner:
    def __init__(self, *, account_service: AccountService, log_dir: Path | None = None) -> None:
        self._account_service = account_service
        self._log_dir = log_dir

    def run_once(self, reason: str) -> AccountSnapshot:
        snapshot = self._account_service.create_snapshot()
        if self._log_dir is not None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self._log_dir / "account-snapshots.jsonl"
            payload = {
                "reason": reason,
                "snapshot": snapshot.model_dump(mode="json"),
            }
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(json.dumps(payload, ensure_ascii=False))
                log_file.write("\n")
        return snapshot

    def start(self, *, interval_seconds: int, timezone: str) -> None:
        scheduler = BlockingScheduler(timezone=timezone)
        scheduler.add_job(
            lambda: self.run_once("intraday"),
            trigger="interval",
            seconds=interval_seconds,
            id="account_snapshot_intraday",
            max_instances=1,
            replace_existing=True,
        )
        scheduler.start()
