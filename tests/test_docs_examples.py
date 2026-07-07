from pathlib import Path


def test_readme_documents_manual_ledger_commands() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "qt ledger add" in readme
    assert "qt ledger list" in readme
    assert "qt service check" in readme
    assert 'pip install -e ".[dev]"' in readme
    assert "bash scripts/start-backend.sh" in readme
    assert "docker compose build" in readme
    assert "docker compose run --rm qt qt service check" in readme
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


def test_docs_document_cash_and_account_service_scope() -> None:
    project_spec = Path("docs/project-spec.md").read_text(encoding="utf-8")
    data_sources = Path("docs/data-sources.md").read_text(encoding="utf-8")
    trading_policy = Path("docs/trading-policy.md").read_text(encoding="utf-8")
    recommendation_contract = Path("docs/recommendation-contract.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "手动资金账户" in project_spec
    assert "账户估值" in project_spec
    assert "调试版后台服务" in project_spec
    assert "手动资金账户" in data_sources
    assert "AkShare 只提供行情报价" in data_sources
    assert "净本金" in trading_policy
    assert "可用买入资金" in trading_policy
    assert "资金上下文" in recommendation_contract
    assert "qt cash init" in readme
    assert "qt account snapshot" in readme
    assert "```bash\nqt service run\n```" in readme
    assert "qt service debug-run --once" in readme
    assert "QT_LOG_DIR=data/logs" in env_example
    assert "QT_MARKET_PROVIDER=akshare" in env_example
    assert "QT_TIMEZONE=Asia/Shanghai" in env_example
