import json
from datetime import UTC, datetime

from quantitative_trading.account.models import AccountSnapshot, AccountSnapshotStatus
from quantitative_trading.runtime import service_runner
from quantitative_trading.runtime.service_runner import DebugServiceRunner


class FakeAccountService:
    def __init__(self) -> None:
        self.calls = 0

    def create_snapshot(self) -> AccountSnapshot:
        self.calls += 1
        return AccountSnapshot(
            positions=[],
            status=AccountSnapshotStatus.CASH_NOT_INITIALIZED,
            warnings=["cash account not initialized"],
            created_at=datetime(2026, 7, 7, 2, 0, tzinfo=UTC),
        )


def test_debug_runner_runs_one_snapshot_cycle() -> None:
    account_service = FakeAccountService()
    runner = DebugServiceRunner(account_service=account_service)

    snapshot = runner.run_once(reason="test")

    assert account_service.calls == 1
    assert snapshot.status is AccountSnapshotStatus.CASH_NOT_INITIALIZED


def test_debug_runner_appends_jsonl_snapshots(tmp_path) -> None:
    account_service = FakeAccountService()
    runner = DebugServiceRunner(account_service=account_service, log_dir=tmp_path)

    runner.run_once(reason="startup")
    runner.run_once(reason="manual")

    log_path = tmp_path / "account-snapshots.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    payloads = [json.loads(line) for line in lines]

    assert len(payloads) == 2
    assert [payload["reason"] for payload in payloads] == ["startup", "manual"]
    assert payloads[0]["snapshot"]["status"] == "cash_not_initialized"
    assert payloads[0]["snapshot"]["warnings"] == ["cash account not initialized"]


def test_debug_runner_starts_intraday_interval_job(monkeypatch) -> None:
    created_schedulers = []

    class FakeScheduler:
        def __init__(self, *, timezone: str) -> None:
            self.timezone = timezone
            self.jobs = []
            self.started = False
            created_schedulers.append(self)

        def add_job(self, func, **kwargs) -> None:
            self.jobs.append((func, kwargs))

        def start(self) -> None:
            self.started = True

    monkeypatch.setattr(service_runner, "BlockingScheduler", FakeScheduler)
    runner = DebugServiceRunner(account_service=FakeAccountService())

    runner.start(interval_seconds=60, timezone="Asia/Shanghai")

    scheduler = created_schedulers[0]
    assert scheduler.timezone == "Asia/Shanghai"
    assert scheduler.started is True
    assert len(scheduler.jobs) == 1
    _, job_kwargs = scheduler.jobs[0]
    assert job_kwargs == {
        "trigger": "interval",
        "seconds": 60,
        "id": "account_snapshot_intraday",
        "max_instances": 1,
        "replace_existing": True,
    }
