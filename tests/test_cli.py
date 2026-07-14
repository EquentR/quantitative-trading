import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import quantitative_trading.cli as cli
from quantitative_trading.cli import app
from quantitative_trading.audit.repository import AuditLogRepository
from quantitative_trading.config import Settings
from quantitative_trading.email.models import SmtpSecurity, SmtpSettingsUpdate
from quantitative_trading.email.outbox import (
    EmailDeliveryRepository,
    EmailDeliveryService,
)
from quantitative_trading.email.repository import SmtpSettingsRepository
from quantitative_trading.email.service import SmtpSettingsService
from quantitative_trading.market.adapters import MarketProviderError
from quantitative_trading.market.calendar import XSHGTradingCalendar
from quantitative_trading.market.models import (
    CaptureDataset,
    CaptureResultStatus,
    CaptureRunStatus,
    DailyBar,
    DailyMoneyFlow,
    MarketCaptureRun,
    MinuteBar,
    QuoteSnapshot,
    QuoteStatus,
    MarketCaptureResult,
)
from quantitative_trading.market.repositories import (
    MarketCaptureResultRepository,
    MarketCaptureRunRepository,
    MinuteBarRepository,
)
from quantitative_trading.notification.models import (
    NotificationStatus,
    NotificationSummary,
)
from quantitative_trading.notification.repository import NotificationRepository
from quantitative_trading.planning.repository import TradingPlanRepository
from quantitative_trading.storage.sqlite import connect, migrate
from quantitative_trading.storage.scheduler_state import SchedulerStateRepository
from tests.planning_fixtures import persist_test_plan


runner = CliRunner()


def run_cli(
    tmp_path: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input: str | None = None,
):
    db_path = tmp_path / "ledger.db"
    cli_env = {"QT_DATABASE_PATH": str(db_path)}
    if env is not None:
        cli_env.update(env)
    return runner.invoke(app, [*args], env=cli_env, input=input)


def test_ledger_add_and_list(tmp_path) -> None:
    add_result = run_cli(
        tmp_path,
        "ledger",
        "add",
        "--symbol",
        "600000",
        "--name",
        "浦发银行",
        "--quantity",
        "1000",
        "--available-quantity",
        "800",
        "--cost-price",
        "9.5",
        "--opened-at",
        "2026-07-06",
    )
    list_result = run_cli(tmp_path, "ledger", "list")

    assert add_result.exit_code == 0
    assert "已新增持仓 600000 浦发银行" in add_result.output
    assert list_result.exit_code == 0
    assert "600000" in list_result.output
    assert "浦发银行" in list_result.output
    assert "数量=1000" in list_result.output
    assert "可用=800" in list_result.output
    assert "成本=9.5" in list_result.output
    assert "更新=" in list_result.output


def test_ledger_update_and_remove(tmp_path) -> None:
    run_cli(
        tmp_path,
        "ledger",
        "add",
        "--symbol",
        "600000",
        "--name",
        "浦发银行",
        "--quantity",
        "1000",
        "--available-quantity",
        "800",
        "--cost-price",
        "9.5",
        "--opened-at",
        "2026-07-06",
    )
    update_result = run_cli(
        tmp_path,
        "ledger",
        "update",
        "600000",
        "--name",
        "浦发银行",
        "--quantity",
        "1200",
        "--available-quantity",
        "1000",
        "--cost-price",
        "9.4",
        "--opened-at",
        "2026-07-06",
        "--note",
        "手动调整",
    )
    remove_result = run_cli(tmp_path, "ledger", "remove", "600000")
    list_result = run_cli(tmp_path, "ledger", "list")

    assert update_result.exit_code == 0
    assert "已更新持仓 600000" in update_result.output
    assert remove_result.exit_code == 0
    assert "已删除持仓 600000" in remove_result.output
    assert "暂无持仓" in list_result.output


def test_ledger_import_and_export(tmp_path) -> None:
    csv_path = tmp_path / "positions.csv"
    csv_path.write_text(
        "symbol,name,quantity,available_quantity,cost_price,opened_at,note\n"
        "600000,浦发银行,1000,800,9.5,2026-07-06,首批\n",
        encoding="utf-8",
    )

    import_result = run_cli(tmp_path, "ledger", "import", str(csv_path))
    export_result = run_cli(tmp_path, "ledger", "export")

    assert import_result.exit_code == 0
    assert "已导入 1 条持仓" in import_result.output
    assert export_result.exit_code == 0
    assert (
        "symbol,name,quantity,available_quantity,cost_price,opened_at,note"
        in export_result.output
    )
    assert "600000,浦发银行,1000,800,9.5,2026-07-06,首批" in export_result.output


def test_ledger_import_reports_duplicate_symbol_error(tmp_path) -> None:
    csv_path = tmp_path / "positions.csv"
    csv_path.write_text(
        "symbol,name,quantity,available_quantity,cost_price,opened_at,note\n"
        "600000,浦发银行,1000,800,9.5,2026-07-06,首批\n"
        "600000,浦发银行,1000,800,9.5,2026-07-06,重复\n",
        encoding="utf-8",
    )

    result = run_cli(tmp_path, "ledger", "import", str(csv_path))

    assert result.exit_code != 0
    assert "导入持仓失败" in result.output
    assert "duplicate symbol 600000" in result.output
    assert "Traceback" not in result.output


def test_ledger_import_reports_missing_file_error(tmp_path) -> None:
    csv_path = tmp_path / "missing.csv"

    result = run_cli(tmp_path, "ledger", "import", str(csv_path))

    assert result.exit_code != 0
    assert "导入持仓失败" in result.output
    assert "missing.csv" in result.output
    assert "Traceback" not in result.output


def test_watchlist_add_list_update_and_remove(tmp_path) -> None:
    add_result = run_cli(
        tmp_path,
        "watchlist",
        "add",
        "--symbol",
        "600000",
        "--name",
        "浦发银行",
        "--rank",
        "1",
        "--plan-enabled",
        "false",
        "--note",
        "观察",
    )
    update_result = run_cli(
        tmp_path,
        "watchlist",
        "update",
        "600000",
        "--name",
        "浦发银行",
        "--rank",
        "1",
        "--plan-enabled",
        "true",
        "--note",
        "观察",
    )
    list_result = run_cli(tmp_path, "watchlist", "list")
    remove_result = run_cli(tmp_path, "watchlist", "remove", "600000")
    empty_result = run_cli(tmp_path, "watchlist", "list")

    assert add_result.exit_code == 0
    assert "已新增观察 600000 浦发银行" in add_result.output
    assert update_result.exit_code == 0
    assert "已更新观察 600000" in update_result.output
    assert list_result.exit_code == 0
    assert "600000" in list_result.output
    assert "计划=true" in list_result.output
    assert "来源=manual" in list_result.output
    assert remove_result.exit_code == 0
    assert "已删除观察 600000" in remove_result.output
    assert "暂无观察股" in empty_result.output


def test_watchlist_import_and_export(tmp_path) -> None:
    csv_path = tmp_path / "watchlist.csv"
    csv_path.write_text(
        "symbol,name,rank,plan_enabled,note\n600000,浦发银行,1,true,观察\n",
        encoding="utf-8",
    )

    import_result = run_cli(tmp_path, "watchlist", "import", str(csv_path))
    export_result = run_cli(tmp_path, "watchlist", "export")

    assert import_result.exit_code == 0
    assert "已导入 1 条观察" in import_result.output
    assert export_result.exit_code == 0
    assert "symbol,name,rank,plan_enabled,note" in export_result.output
    assert "600000,浦发银行,1,true,观察" in export_result.output


def test_watchlist_import_reports_missing_file_error(tmp_path) -> None:
    csv_path = tmp_path / "missing-watchlist.csv"

    result = run_cli(tmp_path, "watchlist", "import", str(csv_path))

    assert result.exit_code != 0
    assert "导入观察失败" in result.output
    assert "missing-watchlist.csv" in result.output
    assert "Traceback" not in result.output


def test_watchlist_import_reports_invalid_header_error(tmp_path) -> None:
    csv_path = tmp_path / "watchlist.csv"
    csv_path.write_text(
        "symbol,name,rank,plan_enabled,note,extra\n600000,浦发银行,1,true,观察,x\n",
        encoding="utf-8",
    )

    result = run_cli(tmp_path, "watchlist", "import", str(csv_path))

    assert result.exit_code != 0
    assert "导入观察失败" in result.output
    assert "CSV header must exactly match" in result.output
    assert "Traceback" not in result.output


def test_service_check_reads_ledger(tmp_path) -> None:
    db_path = tmp_path / "ledger.db"

    result = run_cli(tmp_path, "service", "check")

    assert result.exit_code == 0
    assert "服务检查通过" in result.output
    assert "当前持仓数量: 0" in result.output
    assert not db_path.exists()


def test_service_check_reads_existing_ledger_without_writing(tmp_path) -> None:
    db_path = tmp_path / "ledger.db"
    add_result = run_cli(
        tmp_path,
        "ledger",
        "add",
        "--symbol",
        "600000",
        "--name",
        "浦发银行",
        "--quantity",
        "1000",
        "--available-quantity",
        "800",
        "--cost-price",
        "9.5",
        "--opened-at",
        "2026-07-06",
    )

    result = run_cli(tmp_path, "service", "check")

    assert add_result.exit_code == 0
    assert db_path.exists()
    assert result.exit_code == 0
    assert "服务检查通过" in result.output
    assert "当前持仓数量: 1" in result.output


def test_service_run_command_is_registered(tmp_path) -> None:
    result = run_cli(tmp_path, "service", "run", "--help")

    assert result.exit_code == 0
    assert "unified http api" in result.output.lower()


def test_service_status_reports_persisted_scheduler_state_in_human_and_json(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")
    started_at = datetime(2026, 7, 14, 2, 0, tzinfo=UTC)
    finished_at = datetime(2026, 7, 14, 2, 1, tzinfo=UTC)
    with connect(settings) as connection:
        migrate(connection)
        repository = SchedulerStateRepository(connection)
        repository.set_enabled(
            True,
            interval_seconds=180,
            run_on_start=False,
            now=started_at,
        )
        repository.record_result(
            started_at=started_at,
            finished_at=finished_at,
            status="degraded",
            reason="scheduled_intraday",
            error="minute data incomplete",
            snapshot_id=17,
            task_type="intraday",
            plan_id="plan-20260714-v1",
            recommendation_ids=["rec-1"],
            now=finished_at,
        )

    human = run_cli(tmp_path, "service", "status")
    machine = run_cli(tmp_path, "service", "status", "--json")

    assert human.exit_code == machine.exit_code == 0
    assert "scheduler_enabled=true" in human.output
    assert "last_task_type=intraday" in human.output
    assert "last_status=degraded" in human.output
    assert "last_snapshot_id=17" in human.output
    payload = json.loads(machine.output)
    assert payload == {
        "scheduler_enabled": True,
        "interval_seconds": 180,
        "run_on_start": False,
        "last_started_at": started_at.isoformat(),
        "last_finished_at": finished_at.isoformat(),
        "last_status": "degraded",
        "last_reason": "scheduled_intraday",
        "last_error": "minute data incomplete",
        "last_snapshot_id": 17,
        "last_task_type": "intraday",
        "last_plan_id": "plan-20260714-v1",
        "last_recommendation_ids": ["rec-1"],
        "overrun_count": 0,
        "skipped_count": 0,
        "updated_at": finished_at.isoformat(),
    }


def test_service_status_does_not_create_database_when_missing(tmp_path) -> None:
    database_path = tmp_path / "ledger.db"

    human = run_cli(tmp_path, "service", "status")
    machine = run_cli(tmp_path, "service", "status", "--json")

    assert human.exit_code == machine.exit_code == 0
    assert "scheduler_state=missing" in human.output
    assert json.loads(machine.output) == {"scheduler_state": "missing"}
    assert not database_path.exists()


def test_service_run_starts_api_service(monkeypatch, tmp_path) -> None:
    starts = []

    def fake_run_api_service(settings):
        starts.append(
            {
                "host": settings.api_host,
                "port": settings.api_port,
                "database_path": str(settings.database_path),
            }
        )

    monkeypatch.setattr(cli, "run_api_service", fake_run_api_service)

    result = run_cli(
        tmp_path,
        "service",
        "run",
        env={"QT_API_HOST": "127.0.0.1", "QT_API_PORT": "8123"},
    )

    assert result.exit_code == 0
    assert starts == [
        {
            "host": "127.0.0.1",
            "port": 8123,
            "database_path": str(tmp_path / "ledger.db"),
        }
    ]


def test_service_debug_run_is_retired_without_writing_log(tmp_path) -> None:
    log_dir = tmp_path / "logs"

    result = run_cli(
        tmp_path,
        "service",
        "debug-run",
        "--once",
        env={"QT_LOG_DIR": str(log_dir)},
    )

    assert result.exit_code != 0
    assert "qt workflow intraday" in result.output
    assert not (log_dir / "account-snapshots.jsonl").exists()


def test_service_debug_run_does_not_start_legacy_runner(
    monkeypatch,
    tmp_path,
) -> None:
    del monkeypatch

    result = run_cli(
        tmp_path,
        "service",
        "debug-run",
        env={
            "QT_INTRADAY_INTERVAL_SECONDS": "7",
            "QT_TIMEZONE": "Asia/Shanghai",
        },
    )

    assert result.exit_code != 0
    assert "qt workflow intraday" in result.output


def test_cash_init_show_and_transfer_commands(tmp_path) -> None:
    init_result = run_cli(
        tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal"
    )
    show_result = run_cli(tmp_path, "cash", "show")
    transfer_result = run_cli(
        tmp_path,
        "cash",
        "transfer-in",
        "--amount",
        "10000",
        "--note",
        "bank transfer in",
    )
    final_show_result = run_cli(tmp_path, "cash", "show")

    assert init_result.exit_code == 0
    assert "cash_balance=50000.00" in init_result.output
    assert "net_principal=50000.00" in init_result.output
    assert show_result.exit_code == 0
    assert "cash_balance=50000.00" in show_result.output
    assert "net_principal=50000.00" in show_result.output
    assert transfer_result.exit_code == 0
    assert "transfer_in=10000.00" in transfer_result.output
    assert "cash_balance=60000.00" in final_show_result.output


def test_cash_show_json_outputs_account_model(tmp_path) -> None:
    run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal")

    result = run_cli(tmp_path, "cash", "show", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["cash_balance"] == 50000
    assert payload["total_transfer_in"] == 50000
    assert payload["total_transfer_out"] == 0
    assert payload["net_principal"] == 50000


def test_cash_show_json_reports_not_initialized_as_json(tmp_path) -> None:
    result = run_cli(tmp_path, "cash", "show", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload == {
        "status": "cash_not_initialized",
        "warning": "cash account not initialized",
    }


def test_cash_show_reports_not_initialized(tmp_path) -> None:
    result = run_cli(tmp_path, "cash", "show")

    assert result.exit_code == 0
    assert "cash account not initialized" in result.output


def test_cash_init_rejects_duplicate_initialization_without_traceback(tmp_path) -> None:
    first_result = run_cli(
        tmp_path, "cash", "init", "--cash", "1000", "--note", "initial principal"
    )

    second_result = run_cli(
        tmp_path,
        "cash",
        "init",
        "--cash",
        "1000",
        "--note",
        "duplicate principal",
    )

    assert first_result.exit_code == 0
    assert second_result.exit_code != 0
    assert "already initialized" in second_result.output
    assert "Traceback" not in second_result.output


def test_cash_transfer_out_rejects_excess_cash(tmp_path) -> None:
    run_cli(tmp_path, "cash", "init", "--cash", "1000", "--note", "initial principal")

    result = run_cli(
        tmp_path,
        "cash",
        "transfer-out",
        "--amount",
        "1001",
        "--note",
        "too much cash out",
    )

    assert result.exit_code != 0
    assert "cannot exceed cash balance" in result.output
    assert "Traceback" not in result.output


def test_cash_transfer_out_rejects_amount_above_net_principal_after_adjustment(
    tmp_path,
) -> None:
    run_cli(tmp_path, "cash", "init", "--cash", "1000", "--note", "initial principal")
    run_cli(
        tmp_path,
        "cash",
        "adjust",
        "--cash",
        "1500",
        "--note",
        "manual broker correction",
    )

    result = run_cli(
        tmp_path,
        "cash",
        "transfer-out",
        "--amount",
        "1200",
        "--note",
        "above principal",
    )

    assert result.exit_code != 0
    assert "cannot exceed net principal" in result.output
    assert "Traceback" not in result.output


def test_cash_adjust_changes_cash_without_changing_net_principal(tmp_path) -> None:
    run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal")

    adjust_result = run_cli(
        tmp_path,
        "cash",
        "adjust",
        "--cash",
        "48000",
        "--note",
        "manual broker correction",
    )
    show_result = run_cli(tmp_path, "cash", "show", "--json")

    assert adjust_result.exit_code == 0
    assert "cash_balance=48000.00" in adjust_result.output
    payload = json.loads(show_result.output)
    assert payload["cash_balance"] == 48000
    assert payload["net_principal"] == 50000


def test_cash_transactions_lists_recent_cash_events(tmp_path) -> None:
    run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal")
    run_cli(
        tmp_path,
        "cash",
        "transfer-in",
        "--amount",
        "10000",
        "--note",
        "bank transfer in",
    )

    result = run_cli(tmp_path, "cash", "transactions", "--limit", "5")

    assert result.exit_code == 0
    assert "initial_deposit" in result.output
    assert "transfer_in" in result.output
    assert "cash_before=0.00" in result.output
    assert "cash_after=50000.00" in result.output
    assert "cash_before=50000.00" in result.output
    assert "cash_after=60000.00" in result.output


def test_cash_transactions_limit_uses_repository_order(tmp_path) -> None:
    run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal")
    run_cli(
        tmp_path,
        "cash",
        "transfer-in",
        "--amount",
        "1000",
        "--note",
        "bank transfer in",
    )
    run_cli(
        tmp_path,
        "cash",
        "adjust",
        "--cash",
        "52000",
        "--note",
        "manual broker correction",
    )
    run_cli(
        tmp_path,
        "cash",
        "transfer-out",
        "--amount",
        "500",
        "--note",
        "bank transfer out",
    )

    result = run_cli(tmp_path, "cash", "transactions", "--limit", "2")

    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("initial_deposit ")
    assert "cash_before=0.00" in lines[0]
    assert "cash_after=50000.00" in lines[0]
    assert lines[1].startswith("transfer_in ")
    assert "cash_before=50000.00" in lines[1]
    assert "cash_after=51000.00" in lines[1]


def test_account_snapshot_is_retired_without_calling_market_provider(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeAkShareProvider:
        calls: list[list[str]] = []

        def get_quotes(self, symbols):
            self.calls.append(list(symbols))
            return {
                "600000": QuoteSnapshot(
                    symbol="600000",
                    name="Pufa Bank",
                    current_price=10.5,
                    change_pct=1.2,
                    data_time=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                    fetched_at=datetime(2026, 7, 7, 2, 30, 3, tzinfo=UTC),
                    source="akshare",
                    status=QuoteStatus.OK,
                )
            }

    monkeypatch.setattr(
        cli, "AkShareMarketProvider", FakeAkShareProvider, raising=False
    )
    result = run_cli(tmp_path, "account", "snapshot", "--json")

    assert result.exit_code != 0
    assert "qt workflow intraday" in result.output
    assert FakeAkShareProvider.calls == []


def test_market_snapshot_captures_decision_enabled_symbols(
    monkeypatch,
    tmp_path,
) -> None:
    class FakeAkShareMarketProvider:
        calls: list[list[str]] = []

        def get_quotes(self, symbols):
            self.calls.append(list(symbols))
            return {
                symbol: QuoteSnapshot(
                    symbol=symbol,
                    name=f"Stock {symbol}",
                    current_price=10.5,
                    change_pct=1.2,
                    data_time=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                    fetched_at=datetime(2026, 7, 7, 2, 30, 3, tzinfo=UTC),
                    source="akshare",
                    status=QuoteStatus.OK,
                )
                for symbol in symbols
            }

    monkeypatch.setattr(
        cli,
        "AkShareMarketProvider",
        FakeAkShareMarketProvider,
        raising=False,
    )
    add_result = run_cli(
        tmp_path,
        "ledger",
        "add",
        "--symbol",
        "600000",
        "--name",
        "Pufa Bank",
        "--quantity",
        "1000",
        "--available-quantity",
        "800",
        "--cost-price",
        "9.5",
        "--opened-at",
        "2026-07-06",
    )
    watch_result = run_cli(
        tmp_path,
        "watchlist",
        "add",
        "--symbol",
        "000001",
        "--name",
        "Ping An Bank",
        "--rank",
        "1",
        "--plan-enabled",
        "true",
    )

    result = run_cli(
        tmp_path,
        "market",
        "snapshot",
        env={"QT_ENABLE_MARKET_FETCH": "true", "QT_MARKET_PROVIDER": "akshare"},
    )

    assert add_result.exit_code == 0
    assert watch_result.exit_code == 0
    assert result.exit_code == 0, result.output
    assert "market_snapshot_id=1" in result.output
    assert "universe_snapshot_id=1" in result.output
    assert "requested=2" in result.output
    assert "ok=2" in result.output
    assert "partial=0" in result.output
    assert "stale=0" in result.output
    assert "failed=0" in result.output
    assert "data_time=2026-07-07T02:30:00+00:00" in result.output
    assert (
        "warning=\u5386\u53f2K\u7ebf\u5feb\u7167\u672a\u5728\u6b64\u9636\u6bb5\u91c7\u96c6"
        in result.output
    )
    assert (
        "warning=\u8d44\u91d1\u6d41\u5feb\u7167\u672a\u5728\u6b64\u9636\u6bb5\u91c7\u96c6"
        in result.output
    )
    assert (
        "warning=\u5206\u65f6\u5f3a\u5f31\u5feb\u7167\u672a\u5728\u6b64\u9636\u6bb5\u91c7\u96c6"
        in result.output
    )
    assert FakeAkShareMarketProvider.calls == [["000001", "600000"]]


def test_market_snapshot_sanitizes_storage_failure(
    monkeypatch,
    tmp_path,
) -> None:
    database_path = tmp_path / "synthetic-database-secret.db"
    password = "synthetic-password-secret"
    token = "synthetic-token-secret"
    cookie = "synthetic-cookie-secret"
    api_key = "synthetic-api-key-secret"

    def fail_migration(_connection) -> None:
        raise sqlite3.IntegrityError(
            f"raw sqlite failure path={database_path} password={password} "
            f"token={token} cookie={cookie} api_key={api_key}"
        )

    monkeypatch.setattr(cli, "migrate", fail_migration)

    result = run_cli(
        tmp_path,
        "market",
        "snapshot",
        env={
            "QT_DATABASE_PATH": str(database_path),
            "QT_API_ACCESS_PASSWORD": password,
            "QT_API_TOKEN_SECRET": token,
        },
    )

    assert result.exit_code != 0
    assert "market snapshot storage failed" in result.output
    assert "Traceback" not in result.output
    assert "raw sqlite failure" not in result.output
    for sensitive_value in (str(database_path), password, token, cookie, api_key):
        assert sensitive_value not in result.output


def test_market_snapshot_reports_empty_decision_universe(tmp_path) -> None:
    result = run_cli(tmp_path, "market", "snapshot")

    assert result.exit_code == 0
    assert "requested=0" in result.output
    assert "ok=0" in result.output
    assert "partial=0" in result.output
    assert "stale=0" in result.output
    assert "failed=0" in result.output
    assert "data_time=-" in result.output
    assert (
        "warning=\u65e0\u51b3\u7b56\u542f\u7528\u6807\u7684\uff0c\u672a\u8c03\u7528\u884c\u60c5\u6570\u636e\u6e90"
        in result.output
    )


def test_plan_generate_is_deprecated_without_persisting_data(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        migrate(connection)

    result = run_cli(tmp_path, "plan", "generate", "--date", "2026-07-09")

    assert result.exit_code != 0
    assert "qt plan generate is deprecated" in result.output
    assert "qt workflow close" in result.output
    with connect(settings) as connection:
        assert TradingPlanRepository(connection).count() == 0
        universe_count = connection.execute(
            "SELECT COUNT(*) FROM universe_snapshots"
        ).fetchone()[0]
    assert universe_count == 0


def test_plan_latest_reads_existing_plan(tmp_path) -> None:
    persist_test_plan(Settings(database_path=tmp_path / "ledger.db"))

    latest_result = run_cli(tmp_path, "plan", "latest")

    assert latest_result.exit_code == 0
    assert "plan_id=plan-20260709" in latest_result.output
    assert "trading_day=2026-07-09" in latest_result.output


def test_recommendation_scan_is_retired_without_writing(tmp_path) -> None:
    scan_result = run_cli(tmp_path, "recommendations", "scan")
    list_result = run_cli(tmp_path, "recommendations", "list")

    assert scan_result.exit_code != 0
    assert "recommendations scan is retired" in scan_result.output
    assert "workflow intraday" in scan_result.output
    assert list_result.exit_code == 0
    assert "暂无建议" in list_result.output


def test_recommendation_scan_retirement_has_no_traceback(tmp_path) -> None:
    scan_result = run_cli(tmp_path, "recommendations", "scan")

    assert scan_result.exit_code != 0
    assert "recommendations scan is retired" in scan_result.output
    assert "Traceback" not in scan_result.output


def test_retired_service_debug_run_does_not_use_configured_akshare_provider(
    monkeypatch, tmp_path
) -> None:
    class FakeAkShareProvider:
        calls: list[list[str]] = []

        def get_quotes(self, symbols):
            self.calls.append(list(symbols))
            return {
                "600000": QuoteSnapshot(
                    symbol="600000",
                    name="Pufa Bank",
                    current_price=10.5,
                    change_pct=1.2,
                    data_time=datetime(2026, 7, 7, 2, 30, tzinfo=UTC),
                    fetched_at=datetime(2026, 7, 7, 2, 30, 3, tzinfo=UTC),
                    source="akshare",
                    status=QuoteStatus.OK,
                )
            }

    monkeypatch.setattr(
        cli, "AkShareMarketProvider", FakeAkShareProvider, raising=False
    )
    log_dir = tmp_path / "logs"
    run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal")
    run_cli(
        tmp_path,
        "ledger",
        "add",
        "--symbol",
        "600000",
        "--name",
        "娴﹀彂閾惰",
        "--quantity",
        "1000",
        "--available-quantity",
        "800",
        "--cost-price",
        "9.5",
        "--opened-at",
        "2026-07-06",
    )

    result = run_cli(
        tmp_path,
        "service",
        "debug-run",
        "--once",
        env={"QT_LOG_DIR": str(log_dir)},
    )

    assert result.exit_code != 0
    assert "qt workflow intraday" in result.output
    assert not (log_dir / "account-snapshots.jsonl").exists()
    assert FakeAkShareProvider.calls == []


def test_services_closes_connection_when_migrate_fails(monkeypatch) -> None:
    class FakeConnectionManager:
        def __init__(self) -> None:
            self.exited = False
            self.exit_args = None

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb) -> bool:
            self.exited = True
            self.exit_args = (exc_type, exc, tb)
            return False

    connection_cm = FakeConnectionManager()

    def fake_connect(settings):
        return connection_cm

    def fail_migrate(connection) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(cli, "connect", fake_connect)
    monkeypatch.setattr(cli, "migrate", fail_migrate)

    with pytest.raises(RuntimeError, match="migration failed"):
        cli._services()

    assert connection_cm.exited is True
    assert connection_cm.exit_args[0] is RuntimeError


def test_cash_command_cleanup_receives_bad_parameter_exception(monkeypatch) -> None:
    class FakeConnectionManager:
        def __init__(self) -> None:
            self.exit_args = None

        def __exit__(self, exc_type, exc, tb) -> bool:
            self.exit_args = (exc_type, exc, tb)
            return False

    class FakeCashService:
        def transfer_out(self, amount: float, *, note: str):
            raise cli.CashTransferError(
                "transfer-out amount cannot exceed cash balance"
            )

    connection_cm = FakeConnectionManager()

    def fake_services():
        return connection_cm, object(), object(), FakeCashService(), object()

    monkeypatch.setattr(cli, "_services", fake_services)

    result = runner.invoke(app, ["cash", "transfer-out", "--amount", "1"])

    assert result.exit_code != 0
    assert "cannot exceed cash balance" in result.output
    assert connection_cm.exit_args is not None
    assert connection_cm.exit_args[0] is typer.BadParameter


class CliDailyProvider:
    calls: list[tuple[str, date, date, str]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.calendar = XSHGTradingCalendar()

    def get_daily_bars(self, symbol, start_date, end_date, adjustment):
        self.calls.append((symbol, start_date, end_date, adjustment))
        return [
            DailyBar(
                symbol=symbol,
                trade_date=day,
                open=10,
                high=11,
                low=9,
                close=10,
                volume=100,
                amount=1_000,
                source="fake",
                fetched_at=datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
            )
            for day in self.calendar.trading_days(start_date, end_date)
        ]


class CliMoneyFlowProvider:
    calls: list[tuple[str, date, date]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.calendar = XSHGTradingCalendar()

    def get_daily_money_flow(self, symbol, start_date, end_date):
        self.calls.append((symbol, start_date, end_date))
        return [
            DailyMoneyFlow(
                symbol=symbol,
                trade_date=day,
                main_net_amount=1,
                main_net_pct=1,
                super_large_net_amount=1,
                super_large_net_pct=1,
                large_net_amount=1,
                large_net_pct=1,
                medium_net_amount=-1,
                medium_net_pct=-1,
                small_net_amount=-1,
                small_net_pct=-1,
                source="fake",
                fetched_at=datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
            )
            for day in self.calendar.trading_days(start_date, end_date)
        ]


def install_cli_heavy_providers(monkeypatch) -> None:
    CliDailyProvider.calls = []
    CliMoneyFlowProvider.calls = []
    monkeypatch.setattr(cli, "AkShareDailyBarProvider", CliDailyProvider)
    monkeypatch.setattr(cli, "AkShareMoneyFlowProvider", CliMoneyFlowProvider)


def test_market_backfill_defaults_to_decision_enabled_universe_and_json(
    monkeypatch,
    tmp_path,
) -> None:
    install_cli_heavy_providers(monkeypatch)
    run_cli(
        tmp_path,
        "ledger",
        "add",
        "--symbol",
        "600000",
        "--name",
        "Pufa Bank",
        "--quantity",
        "1000",
        "--available-quantity",
        "1000",
        "--cost-price",
        "9.5",
        "--opened-at",
        "2026-07-06",
    )
    run_cli(
        tmp_path,
        "watchlist",
        "add",
        "--symbol",
        "000001",
        "--name",
        "Ping An Bank",
        "--rank",
        "1",
        "--plan-enabled",
        "true",
    )
    run_cli(
        tmp_path,
        "watchlist",
        "add",
        "--symbol",
        "000002",
        "--name",
        "Vanke A",
        "--rank",
        "2",
        "--plan-enabled",
        "false",
    )

    result = run_cli(
        tmp_path,
        "market",
        "backfill",
        "--date",
        "2026-07-13",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workflow_type"] == "backfill"
    assert payload["status"] == "succeeded"
    assert payload["symbols"] == ["000001", "600000"]
    assert payload["requested_symbols"] == 2
    assert payload["processed_symbols"] == 2
    assert {call[0] for call in CliDailyProvider.calls} == {"000001", "600000"}
    assert {call[0] for call in CliMoneyFlowProvider.calls} == {"000001", "600000"}

    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        results = MarketCaptureResultRepository(connection).list_for_run(
            payload["run_id"]
        )
        audits = AuditLogRepository(connection).list_recent(limit=20)
    assert len(results) == 4
    manual_audit = next(item for item in audits if item.event_type == "workflow.manual_run")
    assert manual_audit.payload["workflow_type"] == "backfill"
    assert manual_audit.payload["run_id"] == payload["run_id"]


def test_market_backfill_explicit_symbols_filter_enabled_universe_and_reuse_run(
    monkeypatch,
    tmp_path,
) -> None:
    install_cli_heavy_providers(monkeypatch)
    for symbol in ("600000", "000001"):
        run_cli(
            tmp_path,
            "watchlist",
            "add",
            "--symbol",
            symbol,
            "--name",
            symbol,
            "--rank",
            "1",
            "--plan-enabled",
            "true",
        )

    first = run_cli(
        tmp_path,
        "market",
        "backfill",
        "--date",
        "2026-07-13",
        "--symbol",
        "600000",
        "--symbol",
        "000001",
        "--json",
    )
    second = run_cli(
        tmp_path,
        "market",
        "backfill",
        "--date",
        "2026-07-13",
        "--symbol",
        "000001",
        "--symbol",
        "600000",
        "--json",
    )

    assert first.exit_code == second.exit_code == 0
    assert json.loads(first.output)["reused"] is False
    assert json.loads(second.output)["reused"] is True
    assert len(CliDailyProvider.calls) == 2
    assert len(CliMoneyFlowProvider.calls) == 2


def test_market_backfill_rejects_symbol_outside_decision_enabled_universe(
    monkeypatch,
    tmp_path,
) -> None:
    install_cli_heavy_providers(monkeypatch)

    result = run_cli(
        tmp_path,
        "market",
        "backfill",
        "--date",
        "2026-07-13",
        "--symbol",
        "600000",
    )

    assert result.exit_code != 0
    assert "market backfill failed" in result.output
    assert CliDailyProvider.calls == []
    assert CliMoneyFlowProvider.calls == []


def test_market_cleanup_retains_twenty_sessions_and_outputs_json(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")
    calendar = XSHGTradingCalendar()
    days = calendar.sessions_ending(date(2026, 7, 13), 22)
    with connect(settings) as connection:
        migrate(connection)
        repository = MinuteBarRepository(connection)
        for day in days:
            repository.upsert_many(
                [
                    MinuteBar(
                        symbol="600000",
                        trade_date=day,
                        minute=datetime(
                            day.year,
                            day.month,
                            day.day,
                            10,
                            0,
                            tzinfo=calendar.timezone,
                        ),
                        open=10,
                        high=10,
                        low=10,
                        close=10,
                        volume=100,
                        amount=1_000,
                        source="fake",
                        fetched_at=datetime(2026, 7, 13, 7, 0, tzinfo=UTC),
                    )
                ]
            )

    result = run_cli(
        tmp_path,
        "market",
        "cleanup",
        "--date",
        "2026-07-13",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["workflow_type"] == "cleanup"
    assert payload["status"] == "succeeded"
    assert payload["deleted_rows"] == 2
    with connect(settings) as connection:
        assert MinuteBarRepository(connection).trade_dates("600000") == days[-20:]


def test_market_runs_lists_latest_run_with_limit_and_json(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        migrate(connection)
        repository = MarketCaptureRunRepository(connection)
        for index in range(2):
            repository.get_or_create(
                MarketCaptureRun(
                    run_id=f"run-{index}",
                    workflow_type="backfill",
                    trade_date=date(2026, 7, 10 + index),
                    idempotency_key=f"backfill:{index}",
                    status=CaptureRunStatus.SUCCEEDED,
                    started_at=datetime(2026, 7, 13, 7, index, tzinfo=UTC),
                    finished_at=datetime(2026, 7, 13, 7, index, 30, tzinfo=UTC),
                    requested_symbols=1,
                    processed_symbols=1,
                )
            )
        MarketCaptureResultRepository(connection).upsert(
            MarketCaptureResult(
                run_id="run-1",
                symbol="600000",
                dataset=CaptureDataset.DAILY_BAR,
                status=CaptureResultStatus.DEGRADED,
                fetched_at=datetime(2026, 7, 13, 7, 1, tzinfo=UTC),
                source="fake",
                warning="short window",
            )
        )

    result = run_cli(tmp_path, "market", "runs", "--limit", "1", "--json")

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["count"] == 1
    assert payload["runs"][0]["run_id"] == "run-1"
    assert payload["runs"][0]["status"] == "succeeded"
    assert payload["runs"][0]["dataset_counts"] == {
        "daily_bar": {
            "complete": 0,
            "degraded": 1,
            "failed": 0,
            "stale": 0,
        }
    }

    human = run_cli(tmp_path, "market", "runs", "--limit", "1")
    assert "daily_bar=complete:0,degraded:1,failed:0,stale:0" in human.output


def test_market_backfill_failure_is_sanitized_without_database_path(
    monkeypatch,
    tmp_path,
) -> None:
    database_path = tmp_path / "synthetic-private.db"

    class FailingWorkflow:
        def run_backfill(self, *args, **kwargs):
            raise sqlite3.OperationalError(
                f"raw failure path={database_path} token=synthetic-token-secret"
            )

    monkeypatch.setattr(
        cli,
        "_market_maintenance_workflow",
        lambda connection: FailingWorkflow(),
    )

    result = run_cli(
        tmp_path,
        "market",
        "backfill",
        "--date",
        "2026-07-13",
    )

    assert result.exit_code != 0
    assert "market backfill failed" in result.output
    assert str(database_path) not in result.output
    assert "synthetic-token-secret" not in result.output
    assert "raw failure" not in result.output
    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=20)
    failed = next(
        item for item in audits if item.event_type == "workflow.manual_run_failed"
    )
    assert failed.payload["workflow_type"] == "backfill"
    assert str(database_path) not in failed.model_dump_json()


def test_market_backfill_sparse_usable_data_is_degraded_not_failed(
    monkeypatch,
    tmp_path,
) -> None:
    class SparseDailyProvider(CliDailyProvider):
        def get_daily_bars(self, symbol, start_date, end_date, adjustment):
            return super().get_daily_bars(symbol, end_date, end_date, adjustment)

    class SparseFlowProvider(CliMoneyFlowProvider):
        def get_daily_money_flow(self, symbol, start_date, end_date):
            return super().get_daily_money_flow(symbol, end_date, end_date)

    monkeypatch.setattr(cli, "AkShareDailyBarProvider", SparseDailyProvider)
    monkeypatch.setattr(cli, "AkShareMoneyFlowProvider", SparseFlowProvider)
    run_cli(
        tmp_path,
        "watchlist",
        "add",
        "--symbol",
        "600000",
        "--name",
        "600000",
        "--rank",
        "1",
        "--plan-enabled",
        "true",
    )

    result = run_cli(
        tmp_path,
        "market",
        "backfill",
        "--date",
        "2026-07-13",
        "--symbol",
        "600000",
        "--json",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "degraded"
    assert {item["status"] for item in payload["results"]} == {"degraded"}


def test_market_backfill_provider_failures_persist_failed_run_and_safe_errors(
    monkeypatch,
    tmp_path,
) -> None:
    class RaisingDailyProvider:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_daily_bars(self, *args, **kwargs):
            raise MarketProviderError(
                "daily failed token=synthetic-token-secret at /tmp/private-market.csv"
            )

    class RaisingFlowProvider:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_daily_money_flow(self, *args, **kwargs):
            raise MarketProviderError(
                "flow failed password=synthetic-password at /tmp/private-flow.csv"
            )

    monkeypatch.setattr(cli, "AkShareDailyBarProvider", RaisingDailyProvider)
    monkeypatch.setattr(cli, "AkShareMoneyFlowProvider", RaisingFlowProvider)
    run_cli(
        tmp_path,
        "watchlist",
        "add",
        "--symbol",
        "600000",
        "--name",
        "600000",
        "--rank",
        "1",
        "--plan-enabled",
        "true",
    )

    result = run_cli(
        tmp_path,
        "market",
        "backfill",
        "--date",
        "2026-07-13",
        "--symbol",
        "600000",
        "--json",
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["status"] == "failed"
    assert payload["failure_count"] == 2
    assert {item["status"] for item in payload["results"]} == {"failed"}
    for unsafe in (
        "synthetic-token-secret",
        "synthetic-password",
        "/tmp/private-market.csv",
        "/tmp/private-flow.csv",
    ):
        assert unsafe not in result.output

    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        run_id = payload["run_id"]
        stored = MarketCaptureRunRepository(connection).get(run_id)
        stored_results = MarketCaptureResultRepository(connection).list_for_run(run_id)
        alerts = NotificationRepository(connection).list_recent(limit=10)
    assert stored is not None
    assert stored.status is CaptureRunStatus.FAILED
    assert len(stored_results) == 2
    assert len(alerts) == 1
    assert alerts[0].action == "system_alert"


def _notification(
    notification_id: str,
    *,
    status: NotificationStatus = NotificationStatus.UNREAD,
    created_at: datetime,
) -> NotificationSummary:
    return NotificationSummary(
        notification_id=notification_id,
        recommendation_id=f"rec-{notification_id}",
        symbol="600000",
        action="reduce",
        confidence="medium",
        key_price=10.5,
        reason=["risk rule"],
        risk=["manual review"],
        data_time=created_at,
        audit_id=f"audit-{notification_id}",
        status=status,
        created_at=created_at,
    )


def _seed_smtp(connection, *, password: str) -> None:
    SmtpSettingsService(SmtpSettingsRepository(connection)).update(
        SmtpSettingsUpdate(
            host="smtp.example.test",
            port=587,
            username="robot@example.test",
            password=password,
            sender="robot@example.test",
            recipient="owner@example.test",
            security=SmtpSecurity.STARTTLS,
            enabled=True,
        ),
        now=datetime(2026, 7, 13, 6, 0, tzinfo=UTC),
    )


def test_notifications_cli_lists_counts_and_marks_read_with_audit(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")
    now = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
    with connect(settings) as connection:
        migrate(connection)
        repository = NotificationRepository(connection)
        repository.save(_notification("notif-1", created_at=now))
        repository.save(
            _notification(
                "notif-2",
                created_at=now.replace(minute=1),
            )
        )
        repository.save(
            _notification(
                "notif-3",
                status=NotificationStatus.READ,
                created_at=now.replace(minute=2),
            )
        )

    listed = run_cli(
        tmp_path,
        "notifications",
        "list",
        "--status",
        "unread",
        "--limit",
        "1",
        "--offset",
        "1",
    )
    unread = run_cli(tmp_path, "notifications", "unread")
    listed_json = run_cli(tmp_path, "notifications", "list", "--json")
    unread_json = run_cli(tmp_path, "notifications", "unread", "--json")
    marked = run_cli(tmp_path, "notifications", "read", "notif-1")

    assert listed.exit_code == 0
    assert "notif-1 600000 reduce status=unread" in listed.output
    assert "notif-2" not in listed.output
    assert unread.exit_code == 0
    assert unread.output.strip() == "unread=2"
    assert len(json.loads(listed_json.output)) == 3
    assert json.loads(unread_json.output) == {"unread": 2}
    assert marked.exit_code == 0
    assert "notification_id=notif-1 status=read" in marked.output
    with connect(settings) as connection:
        assert (
            NotificationRepository(connection).get("notif-1").status
            is NotificationStatus.READ
        )
        audits = AuditLogRepository(connection).list_recent(limit=20)
    read_audit = next(item for item in audits if item.event_type == "notification.read")
    assert read_audit.payload == {"notification_id": "notif-1"}


def test_notifications_cli_read_missing_is_clear_and_has_no_traceback(tmp_path) -> None:
    result = run_cli(tmp_path, "notifications", "read", "missing")

    assert result.exit_code != 0
    assert "notification not found: missing" in result.output
    assert "Traceback" not in result.output


def test_email_cli_status_is_explicit_and_never_outputs_password(tmp_path) -> None:
    synthetic_password = "synthetic-cli-smtp-password"
    missing = run_cli(tmp_path, "email", "status")
    missing_test = run_cli(tmp_path, "email", "test")
    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        migrate(connection)
        _seed_smtp(connection, password=synthetic_password)

    configured = run_cli(tmp_path, "email", "status")

    assert missing.exit_code == 0
    assert "configured=false" in missing.output
    assert "enabled=false" in missing.output
    assert "password_configured=false" in missing.output
    assert missing_test.exit_code == 0
    assert missing_test.output.strip() == "smtp_test=not_configured"
    assert configured.exit_code == 0
    assert "configured=true" in configured.output
    assert "enabled=true" in configured.output
    assert "password_configured=true" in configured.output
    assert synthetic_password not in missing.output + configured.output


def test_email_cli_test_uses_sender_and_audits_sanitized_failure(
    monkeypatch, tmp_path
) -> None:
    synthetic_password = "synthetic-cli-smtp-password"
    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        migrate(connection)
        _seed_smtp(connection, password=synthetic_password)

    class FailingSender:
        calls = 0

        def send(self, settings, *, recipient, subject, body):  # noqa: ANN001
            self.calls += 1
            raise RuntimeError(
                f"login failed password={synthetic_password} token=synthetic-token /tmp/mail.log"
            )

    sender = FailingSender()
    monkeypatch.setattr(cli, "SmtplibEmailSender", lambda: sender, raising=False)

    result = run_cli(tmp_path, "email", "test")

    assert result.exit_code != 0
    assert "smtp test failed" in result.output.lower()
    assert "Traceback" not in result.output
    assert sender.calls == 1
    for secret in (synthetic_password, "synthetic-token", "/tmp/mail.log"):
        assert secret not in result.output
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=20)
    failure = next(item for item in audits if item.event_type == "smtp.test.failed")
    audit_text = failure.model_dump_json()
    assert synthetic_password not in audit_text
    assert "synthetic-token" not in audit_text


def test_email_cli_deliveries_and_retry_share_outbox_service_and_write_audit(
    tmp_path,
) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")
    now = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)

    class NoopSender:
        def send(self, settings, *, recipient, subject, body):  # noqa: ANN001
            pass

    with connect(settings) as connection:
        migrate(connection)
        NotificationRepository(connection).save(
            _notification("notif-1", created_at=now)
        )
        repository = EmailDeliveryRepository(connection)
        delivery = EmailDeliveryService(
            repository,
            SmtpSettingsRepository(connection),
            NoopSender(),
            id_factory=lambda: "delivery-1",
        ).enqueue(
            dedup_key="condition-1",
            notification_id="notif-1",
            recipient="owner@example.test",
            subject="Risk alert",
            body="Review locally.",
            payload={},
            now=now,
        )
        connection.execute(
            """
            UPDATE email_deliveries
            SET status = 'dead', attempt_count = 6, next_attempt_at = NULL,
                last_error = 'safe failure'
            WHERE delivery_id = ?
            """,
            (delivery.delivery_id,),
        )
        connection.commit()

    listed = run_cli(
        tmp_path,
        "email",
        "deliveries",
        "--status",
        "dead",
    )
    listed_json = run_cli(tmp_path, "email", "deliveries", "--json")
    retried = run_cli(tmp_path, "email", "retry", "delivery-1")

    assert listed.exit_code == 0
    assert "delivery-1 status=dead attempts=6" in listed.output
    assert json.loads(listed_json.output)[0]["delivery_id"] == "delivery-1"
    assert retried.exit_code == 0
    assert "delivery_id=delivery-1 status=pending attempts=0" in retried.output
    with connect(settings) as connection:
        assert (
            EmailDeliveryRepository(connection).get("delivery-1").status.value
            == "pending"
        )
        audits = AuditLogRepository(connection).list_recent(limit=20)
    retry_audit = next(
        item for item in audits if item.event_type == "email.delivery.retried"
    )
    assert retry_audit.payload == {"delivery_id": "delivery-1"}


def test_workflow_intraday_cli_uses_shared_decision_workflow(
    tmp_path,
    monkeypatch,
) -> None:
    calls = []

    class FakeWorkflow:
        def run_intraday(self):
            calls.append("intraday")
            return type(
                "Result",
                (),
                {
                    "run_id": "intraday-20260714-1000",
                    "reused": False,
                    "status": CaptureRunStatus.SUCCEEDED,
                    "market_input_snapshot_id": 12,
                    "recommendation_ids": ("rec-1",),
                    "warnings": (),
                },
            )()

    monkeypatch.setattr(
        cli,
        "build_decision_workflow",
        lambda connection, settings, now: FakeWorkflow(),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "_workflow_now",
        lambda: datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
        raising=False,
    )

    result = run_cli(tmp_path, "workflow", "intraday")

    assert result.exit_code == 0
    assert calls == ["intraday"]
    assert "run_id=intraday-20260714-1000" in result.output
    assert "recommendations=1" in result.output


def test_workflow_intraday_cli_failed_result_alerts_and_exits_nonzero(
    tmp_path,
    monkeypatch,
) -> None:
    class FailedWorkflow:
        def run_intraday(self):
            return type(
                "Result",
                (),
                {
                    "run_id": "intraday-20260714-1000",
                    "reused": False,
                    "status": CaptureRunStatus.FAILED,
                    "market_input_snapshot_id": 12,
                    "recommendation_ids": (),
                    "warnings": ("all requested quotes unavailable",),
                },
            )()

    monkeypatch.setattr(
        cli,
        "build_decision_workflow",
        lambda connection, settings, now: FailedWorkflow(),
    )
    monkeypatch.setattr(
        cli,
        "_workflow_now",
        lambda: datetime(2026, 7, 14, 2, 0, tzinfo=UTC),
    )

    result = run_cli(tmp_path, "workflow", "intraday")

    assert result.exit_code == 1
    assert "status=failed" in result.output
    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        alerts = NotificationRepository(connection).list_recent(limit=10)
    assert len(alerts) == 1
    assert alerts[0].action == "system_alert"


def test_plan_read_command_supports_json(tmp_path) -> None:
    settings = Settings(database_path=tmp_path / "ledger.db")
    plan = persist_test_plan(settings, trading_day=date(2026, 7, 14))

    latest = run_cli(tmp_path, "plan", "latest", "--json")

    assert latest.exit_code == 0
    assert json.loads(latest.output)["plan_id"] == plan.plan_id


def test_empty_plan_and_recommendation_json_commands_return_stable_empty_values(
    tmp_path,
) -> None:
    latest = run_cli(tmp_path, "plan", "latest", "--json")
    listed = run_cli(tmp_path, "recommendations", "list", "--json")

    assert latest.exit_code == listed.exit_code == 0
    assert json.loads(latest.output) is None
    assert json.loads(listed.output) == []


def test_workflow_close_cli_requires_reason_for_calendar_override_and_audits(
    tmp_path,
    monkeypatch,
) -> None:
    calls = []

    class FakeWorkflow:
        def run_close(self, trade_date, *, skip_calendar=False):
            calls.append((trade_date, skip_calendar))
            return type(
                "Result",
                (),
                {
                    "run_id": "close-20260712",
                    "ready": False,
                    "reused": False,
                    "market_input_snapshot_id": 13,
                    "plan_id": None,
                    "warnings": ("日线未就绪",),
                },
            )()

    monkeypatch.setattr(
        cli,
        "build_decision_workflow",
        lambda connection, settings, now: FakeWorkflow(),
        raising=False,
    )
    monkeypatch.setattr(
        cli,
        "_workflow_now",
        lambda: datetime(2026, 7, 12, 7, 20, tzinfo=UTC),
        raising=False,
    )

    rejected = run_cli(
        tmp_path,
        "workflow",
        "close",
        "--date",
        "2026-07-12",
        "--skip-calendar",
    )
    accepted = run_cli(
        tmp_path,
        "workflow",
        "close",
        "--date",
        "2026-07-12",
        "--skip-calendar",
        "--reason",
        "人工确认交易日历例外",
        env={"QT_API_ACCESS_PASSWORD": "local-password"},
        input="local-password\n",
    )

    assert rejected.exit_code != 0
    assert "reason is required" in rejected.output
    assert accepted.exit_code == 0
    assert calls == [(date(2026, 7, 12), True)]
    assert "run_id=close-20260712" in accepted.output

    settings = Settings(database_path=tmp_path / "ledger.db")
    with connect(settings) as connection:
        audits = AuditLogRepository(connection).list_recent(limit=10)
    assert audits[0].event_type == "workflow.manual_run"
    assert audits[0].payload["manual_reason"] == "人工确认交易日历例外"


def test_workflow_close_cli_rejects_invalid_override_password(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        cli,
        "_workflow_now",
        lambda: datetime(2026, 7, 12, 7, 20, tzinfo=UTC),
    )

    result = run_cli(
        tmp_path,
        "workflow",
        "close",
        "--date",
        "2026-07-12",
        "--skip-calendar",
        "--reason",
        "人工确认交易日历例外",
        env={"QT_API_ACCESS_PASSWORD": "local-password"},
        input="wrong-password\n",
    )

    assert result.exit_code != 0
    assert "manual workflow authentication failed" in result.output
    assert "wrong-password" not in result.output
