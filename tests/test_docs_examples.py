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


def test_readme_documents_manual_market_snapshot_capture_boundaries() -> None:
    section = _markdown_section(
        Path("README.md").read_text(encoding="utf-8"),
        "市场输入快照",
    )

    assert "qt market snapshot" in section
    assert "只采集手动持仓和 `plan_enabled=true` 的非持仓自选标的" in section
    assert "关闭计划的非持仓自选会被排除，重复标的只采集一次" in section
    assert "输出市场快照 ID、股票池快照 ID、请求及状态计数、数据时间和告警" in section
    assert "不会下单、控制交易客户端，也不读取或修改真实券商凭据或账户" in section
    assert "市场快照尚未接入计划、策略、建议、通知或调度" in section
    assert (
        "provider 整体失败或禁用行情抓取时，命令仍可成功退出，因为失败报价已持久化；"
        "调用方必须结合状态计数和 warnings 判断结果，不能只看退出码"
    ) in section


def test_api_docs_document_authenticated_market_snapshot_contract() -> None:
    section = _markdown_section(
        Path("docs/api.md").read_text(encoding="utf-8"),
        "市场快照接口",
    )

    assert "POST /api/v1/market/snapshots" in section
    assert "GET /api/v1/market/snapshots/latest" in section
    assert "GET /api/v1/market/snapshots/{snapshot_id}" in section
    assert (
        "三个接口都是业务接口，必须携带 `Authorization: Bearer <access_token>`；"
        "缺失、格式错误或无效 token 返回 `unauthorized`"
    ) in section
    assert (
        "成功时返回 `201`，响应只包含 `snapshot_id` 和聚合 `snapshot`"
    ) in section
    assert "两个 `GET` 只读取已经保存的聚合快照，不重新请求行情" in section
    assert "稳定错误码 `market_snapshot_not_found`" in section
    assert "`422 validation_error`" in section
    assert "经过清理的 `500 internal_error`" in section
    assert "共享 `MarketSnapshotService` 和当前市场 provider 配置" in section
    assert "不得暴露数据库内容、第三方原始响应或凭据" in section
    assert "不下单、不控制真实交易客户端，也不读取或修改真实券商凭据或账户" in section
    assert "本期快照尚未被计划、策略、建议、通知或调度流程消费" in section


def test_data_source_docs_define_traceable_market_snapshot_semantics() -> None:
    section = _markdown_section(
        Path("docs/data-sources.md").read_text(encoding="utf-8"),
        "8. 首期可追溯行情快照",
    )

    assert (
        "只采集手动持仓，以及 `plan_enabled=true` 的非持仓自选标的。"
        "关闭计划的非持仓自选标的必须排除；同一股票同时来自持仓和自选时只采集一次"
    ) in section
    assert (
        "每个请求标的都必须持久化一条项目内部 `QuoteSnapshot`，并进入聚合快照的 `quote_snapshot_refs`"
    ) in section
    assert (
        "稀疏响应中缺少的股票和 provider 整体失败都保存为 `status=failed` 的失败行，不得静默丢失"
    ) in section
    assert "provider 返回的额外标的必须忽略并告警，不得借外部响应扩大股票池" in section
    assert "`data_time` 是本批次最早的可用报价市场时间" in section
    assert "`fetched_at` 是系统采集和工作流时间，不代表市场报价发生时间" in section
    assert "全部失败或空股票池时允许 `data_time=null`，CLI 显示 `-`" in section
    assert (
        "`history_snapshot_refs`、`money_flow_snapshot_refs` 和 `intraday_strength_snapshot_refs` "
        "固定为空映射，并在聚合快照中保留未采集告警"
    ) in section
    assert "不读取或修改真实券商凭据或账户" in section
    assert "不被计划、策略、建议、通知或调度流程消费" in section
