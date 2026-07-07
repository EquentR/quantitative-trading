from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from quantitative_trading.account.models import AccountSnapshot
from quantitative_trading.account.service import AccountService


class DebugServiceRunner:
    def __init__(
        self,
        *,
        account_service: AccountService | None = None,
        snapshot_factory: Callable[[], AccountSnapshot] | None = None,
        log_dir: Path | None = None,
    ) -> None:
        if (account_service is None) == (snapshot_factory is None):
            raise ValueError("provide exactly one of account_service or snapshot_factory")
        if snapshot_factory is not None:
            self._snapshot_factory = snapshot_factory
        else:
            assert account_service is not None
            self._snapshot_factory = account_service.create_snapshot
        self._log_dir = log_dir

    def run_once(self, *, reason: str) -> AccountSnapshot:
        snapshot = self._snapshot_factory()
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
            lambda: self.run_once(reason="intraday"),
            trigger="interval",
            seconds=interval_seconds,
            id="account_snapshot_intraday",
            max_instances=1,
            replace_existing=True,
        )
        scheduler.start()
