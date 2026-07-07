import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

import quantitative_trading.cli as cli
from quantitative_trading.cli import app
from quantitative_trading.market.models import QuoteSnapshot, QuoteStatus


runner = CliRunner()


def run_cli(tmp_path: Path, *args: str, env: dict[str, str] | None = None):
    db_path = tmp_path / "ledger.db"
    cli_env = {"QT_DATABASE_PATH": str(db_path)}
    if env is not None:
        cli_env.update(env)
    return runner.invoke(app, [*args], env=cli_env)


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
    assert "symbol,name,quantity,available_quantity,cost_price,opened_at,note" in export_result.output
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


def test_service_run_once_outputs_status_and_writes_log(tmp_path) -> None:
    log_dir = tmp_path / "logs"

    result = run_cli(
        tmp_path,
        "service",
        "debug-run",
        "--once",
        env={"QT_LOG_DIR": str(log_dir)},
    )

    assert result.exit_code == 0
    assert "status=cash_not_initialized" in result.output
    log_path = log_dir / "account-snapshots.jsonl"
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["reason"] == "startup"
    assert payload["snapshot"]["status"] == "cash_not_initialized"
    assert payload["snapshot"]["warnings"] == ["cash account not initialized"]


def test_service_run_polling_passes_interval_timezone_and_uses_snapshot_factory(
    monkeypatch,
    tmp_path,
) -> None:
    starts = []
    reasons = []
    factory_statuses = []

    class FakeDebugServiceRunner:
        def __init__(self, *, snapshot_factory, account_service=None, log_dir=None) -> None:
            assert account_service is None
            self.snapshot_factory = snapshot_factory
            self.log_dir = log_dir

        def run_once(self, *, reason: str):
            reasons.append(reason)
            snapshot = self.snapshot_factory()
            factory_statuses.append(snapshot.status.value)
            return snapshot

        def start(self, *, interval_seconds: int, timezone: str) -> None:
            starts.append(
                {
                    "interval_seconds": interval_seconds,
                    "timezone": timezone,
                }
            )
            snapshot = self.snapshot_factory()
            factory_statuses.append(snapshot.status.value)

    monkeypatch.setattr(cli, "DebugServiceRunner", FakeDebugServiceRunner)

    result = run_cli(
        tmp_path,
        "service",
        "debug-run",
        env={
            "QT_INTRADAY_INTERVAL_SECONDS": "7",
            "QT_TIMEZONE": "Asia/Shanghai",
        },
    )

    assert result.exit_code == 0
    assert "debug service started status=cash_not_initialized" in result.output
    assert "debug service polling interval=7s timezone=Asia/Shanghai" in result.output
    assert reasons == ["startup"]
    assert factory_statuses == ["cash_not_initialized", "cash_not_initialized"]
    assert starts == [{"interval_seconds": 7, "timezone": "Asia/Shanghai"}]


def test_cash_init_show_and_transfer_commands(tmp_path) -> None:
    init_result = run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal")
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
    first_result = run_cli(tmp_path, "cash", "init", "--cash", "1000", "--note", "initial principal")

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


def test_cash_transfer_out_rejects_amount_above_net_principal_after_adjustment(tmp_path) -> None:
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
    run_cli(tmp_path, "cash", "transfer-in", "--amount", "1000", "--note", "bank transfer in")
    run_cli(tmp_path, "cash", "adjust", "--cash", "52000", "--note", "manual broker correction")
    run_cli(tmp_path, "cash", "transfer-out", "--amount", "500", "--note", "bank transfer out")

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


def test_account_snapshot_reports_cash_not_initialized(tmp_path) -> None:
    result = run_cli(tmp_path, "account", "snapshot")

    assert result.exit_code == 0
    assert "cash_not_initialized" in result.output


def test_account_snapshot_json_outputs_status(tmp_path) -> None:
    result = run_cli(tmp_path, "account", "snapshot", "--json")

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "cash_not_initialized"
    assert payload["warnings"] == ["cash account not initialized"]


def test_account_snapshot_with_position_uses_configured_akshare_provider(
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

    monkeypatch.setattr(cli, "AkShareMarketProvider", FakeAkShareProvider, raising=False)
    run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal")
    add_result = run_cli(
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

    result = run_cli(tmp_path, "account", "snapshot", "--json")

    assert add_result.exit_code == 0
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["market_value"] == 10500
    assert payload["position_cost"] == 9500
    assert payload["floating_pnl"] == 1000
    assert payload["total_assets"] == 60500
    assert payload["total_pnl"] == 10500
    assert payload["positions"][0]["current_price"] == 10.5
    assert FakeAkShareProvider.calls == [["600000"]]


def test_account_snapshot_with_market_fetch_disabled_reports_warning(tmp_path) -> None:
    run_cli(tmp_path, "cash", "init", "--cash", "50000", "--note", "initial principal")
    add_result = run_cli(
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
        "account",
        "snapshot",
        env={"QT_ENABLE_MARKET_FETCH": "false"},
    )

    assert add_result.exit_code == 0
    assert result.exit_code == 0
    assert "market_data_unavailable" in result.output
    assert "market fetch disabled" in result.output


def test_service_run_once_uses_configured_akshare_provider(monkeypatch, tmp_path) -> None:
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

    monkeypatch.setattr(cli, "AkShareMarketProvider", FakeAkShareProvider, raising=False)
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

    assert result.exit_code == 0
    assert "debug service started status=ok" in result.output
    log_path = log_dir / "account-snapshots.jsonl"
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    assert payload["snapshot"]["status"] == "ok"
    assert payload["snapshot"]["market_value"] == 10500
    assert FakeAkShareProvider.calls == [["600000"]]


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
            raise cli.CashTransferError("transfer-out amount cannot exceed cash balance")

    connection_cm = FakeConnectionManager()

    def fake_services():
        return connection_cm, object(), object(), FakeCashService(), object()

    monkeypatch.setattr(cli, "_services", fake_services)

    result = runner.invoke(app, ["cash", "transfer-out", "--amount", "1"])

    assert result.exit_code != 0
    assert "cannot exceed cash balance" in result.output
    assert connection_cm.exit_args is not None
    assert connection_cm.exit_args[0] is typer.BadParameter
