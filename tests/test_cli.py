import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import quantitative_trading.cli as cli
from quantitative_trading.cli import app


runner = CliRunner()


def run_cli(tmp_path: Path, *args: str):
    db_path = tmp_path / "ledger.db"
    return runner.invoke(app, [*args], env={"QT_DATABASE_PATH": str(db_path)})


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


def test_cash_show_reports_not_initialized(tmp_path) -> None:
    result = run_cli(tmp_path, "cash", "show")

    assert result.exit_code == 0
    assert "cash account not initialized" in result.output


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
    assert "50000.00" in result.output
    assert "60000.00" in result.output


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
