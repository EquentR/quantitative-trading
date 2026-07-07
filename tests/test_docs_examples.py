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


def test_api_docs_document_auth_and_error_contract() -> None:
    text = Path("docs/api.md").read_text(encoding="utf-8")

    assert "POST /api/v1/auth/setup-password" in text
    assert "POST /api/v1/auth/login" in text
    assert (
        "`GET /api/v1/service/status` 未携带令牌时只返回 "
        '`{"auth_status": "setup_required"}` 或 `{"auth_status": "configured"}`'
    ) in text
    assert "`POST /api/v1/auth/login` 在密码未设置前返回 `auth_setup_required`" in text
    assert "`GET /api/v1/auth/me` 必须携带 `Authorization: Bearer <access_token>`" in text
    assert "缺失、格式错误或无效 token 均返回 `unauthorized`" in text
    assert "`POST /api/v1/auth/logout` 不做服务端 token 撤销" in text
    assert "客户端应丢弃本地 token" in text
    assert '"error"' in text
    assert "auth_setup_required" in text
    assert "Authorization: Bearer" in text
    assert "GET /api/v1/account/snapshot?fresh=true" in text
    assert "生成并保存新的账户快照" in text
    assert (
        "业务接口不可用，并返回 `auth_status=setup_required` 或统一错误码 "
        "`auth_setup_required`"
    ) not in text


def test_project_docs_mention_http_api_without_frontend_scope() -> None:
    text = Path("docs/project-spec.md").read_text(encoding="utf-8")

    assert "HTTP API" in text
    assert "不实现前端" in text or "不涉及前端" in text
