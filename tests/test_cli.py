from pathlib import Path

from typer.testing import CliRunner

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


def test_service_check_reads_ledger(tmp_path) -> None:
    result = run_cli(tmp_path, "service", "check")

    assert result.exit_code == 0
    assert "服务检查通过" in result.output
    assert "当前持仓数量: 0" in result.output
