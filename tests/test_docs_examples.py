from pathlib import Path


def test_readme_documents_manual_ledger_commands() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "qt ledger add" in readme
    assert "qt ledger list" in readme
    assert "qt service check" in readme
    assert "不会自动下单" in readme
    assert (
        "qt ledger add --symbol 600000 --name 浦发银行 --quantity 1000 "
        "--available-quantity 1000 --cost-price 9.50 --opened-at 2026-07-06"
    ) in readme
    assert (
        '```powershell\n$env:QT_DATABASE_PATH = "data/quant_trading.db"\n```'
    ) in readme
    assert (
        "```bash\nexport QT_DATABASE_PATH=data/quant_trading.db\n```"
    ) in readme
