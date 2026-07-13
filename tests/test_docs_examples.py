from pathlib import Path


def _markdown_section(text: str, heading: str) -> str:
    """Return one level-two Markdown section, excluding the next section."""
    lines = text.splitlines()
    marker = f"## {heading}"
    start = lines.index(marker) + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


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
    assert ("```bash\nexport QT_DATABASE_PATH=data/quant_trading.db\n```") in readme


def test_docs_document_cash_and_account_service_scope() -> None:
    project_spec = Path("docs/project-spec.md").read_text(encoding="utf-8")
    data_sources = Path("docs/data-sources.md").read_text(encoding="utf-8")
    trading_policy = Path("docs/trading-policy.md").read_text(encoding="utf-8")
    recommendation_contract = Path("docs/recommendation-contract.md").read_text(
        encoding="utf-8"
    )
    readme = Path("README.md").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "手动资金账户" in project_spec
    assert "账户估值" in project_spec
    assert "统一后台服务" in project_spec
    assert "手动资金账户" in data_sources
    assert "AkShare 只提供行情报价" in data_sources
    assert "净本金" in trading_policy
    assert "可用买入资金" in trading_policy
    assert "资金上下文" in recommendation_contract
    assert "qt cash init" in readme
    assert "qt workflow intraday" in readme
    assert "```bash\nqt service run\n```" in readme
    assert "qt service debug-run` 已退役" in readme
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
    assert (
        "`GET /api/v1/auth/me` 必须携带 `Authorization: Bearer <access_token>`" in text
    )
    assert "缺失、格式错误或无效 token 均返回 `unauthorized`" in text
    assert "`POST /api/v1/auth/logout` 不做服务端 token 撤销" in text
    assert "客户端应丢弃本地 token" in text
    assert '"error"' in text
    assert "auth_setup_required" in text
    assert "Authorization: Bearer" in text
    assert "GET /api/v1/account/snapshot?fresh=true" in text
    assert "固定返回 HTTP `410`" in text
    assert "POST /api/v1/account/snapshots" in text
    assert "account_snapshot_create_retired" in text
    assert (
        "业务接口不可用，并返回 `auth_status=setup_required` 或统一错误码 "
        "`auth_setup_required`"
    ) not in text


def test_project_docs_define_http_api_and_local_frontend_scope() -> None:
    text = Path("docs/project-spec.md").read_text(encoding="utf-8")

    assert "HTTP API" in text
    assert "Web 行情工作台" in text
    assert "不自动下单" in text
    assert "不提供下单控件" in text


def test_readme_documents_manual_market_snapshot_capture_boundaries() -> None:
    section = _markdown_section(
        Path("README.md").read_text(encoding="utf-8"),
        "行情数据和市场输入快照",
    )

    assert "qt market snapshot" in section
    assert "重数据只覆盖手动持仓和 `plan_enabled=true` 的非持仓自选" in section
    assert "重复标的只采集一次" in section
    assert "250 个 `XSHG` 交易日的前复权日 K" in section
    assert "60 个交易日" in section
    assert "原始分钟线只保留最近 20 个交易日" in section
    assert "每个 `DecisionWorkflow` 运行形成" in section
    assert "不得用抓取时间替代" in section
    assert "同日已固化前复权日 K 收盘价严格一致" in section


def test_api_docs_document_authenticated_market_snapshot_contract() -> None:
    section = _markdown_section(
        Path("docs/api.md").read_text(encoding="utf-8"),
        "行情、工作流运行和数据引用",
    )

    assert "POST /api/v1/market/snapshots" in section
    assert "GET /api/v1/market/snapshots/latest" in section
    assert "GET /api/v1/market/snapshots/{snapshot_id}" in section
    assert "GET /api/v1/market/symbols/{symbol}/daily-bars?limit=250" in section
    assert "GET /api/v1/market/symbols/{symbol}/money-flow?limit=60" in section
    assert "GET /api/v1/market/snapshots/{snapshot_id}/trace?symbol=600000" in section
    assert "这些接口都是认证业务接口" in section
    assert "不因 Web 刷新重新请求 AkShare" in section
    assert "前端不得补算这些字段" in section
    assert "不得返回第三方原始 payload、数据库路径或凭据" in section


def test_current_docs_deprecate_legacy_plan_writers() -> None:
    api_docs = Path("docs/api.md").read_text(encoding="utf-8")
    project_spec = Path("docs/project-spec.md").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "`POST /api/v1/plans` 已废弃" in api_docs
    assert "`plan_write_deprecated`" in api_docs
    assert "`qt plan generate` 已废弃" in api_docs
    assert "POST /api/v1/service/workflows/close/run" in api_docs
    assert "qt workflow close" in api_docs
    assert "生成某日交易计划" not in project_spec
    assert "qt plan generate --date" not in readme


def test_data_source_docs_define_traceable_market_snapshot_semantics() -> None:
    section = _markdown_section(
        Path("docs/data-sources.md").read_text(encoding="utf-8"),
        "8. 行情决策数据与可追溯快照",
    )

    assert "重数据和决策只覆盖手动持仓" in section
    assert "`plan_enabled=true` 的非持仓自选标的" in section
    assert "provider 返回的额外股票必须忽略并告警，不能借外部响应扩大股票池" in section
    assert "最近 250 个 `XSHG` 交易日" in section
    assert "最近 60 个 `XSHG` 交易日" in section
    assert "原始分钟线滚动保留最近 20 个 `XSHG` 交易日" in section
    assert "`history_snapshot_refs`" in section
    assert "`money_flow_snapshot_refs`" in section
    assert "`intraday_strength_snapshot_refs`" in section
    assert "计划、策略、风控、建议、通知和审计只能引用本轮已经固化的输入" in section
    assert "不读取或修改真实券商账户" in section
