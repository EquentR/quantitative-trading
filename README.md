# quantitative-trading

个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不会自动下单，不控制真实交易客户端，不绕过人工确认。

后台调度器、CLI、HTTP API 和本地 Web 控制台共享后端 `DecisionWorkflow`。每轮工作流只采集并固化一次标准化输入，再依次执行特征、计划、策略、风控、建议、通知和审计；Web 不计算策略或风险，也不提供真实交易控件。

## 手动持仓台账

首版以手动持仓台账作为真实持仓、成本价、持仓数量和可用数量的唯一权威源。

## 自选置顶观察池

自选置顶观察池是候选买入股票池来源，不代表真实持仓。观察池记录 `symbol,name,rank,plan_enabled,note`，并由系统记录来源：

- `manual`：本地手动维护。
- `synced`：外部自选置顶同步结果，默认不进入计划。
- `manual_synced`：外部同步命中本地手动记录，保留本地 `plan_enabled` 和备注。

非持仓自选股票只有 `plan_enabled=true` 时才会进入收盘计划和盘中决策；持仓台账中的股票始终按持仓来源进入计划。

## 本地开发安装

项目依赖声明在 `pyproject.toml` 中，不使用 `requirements.txt`。推荐使用虚拟环境安装运行依赖和测试依赖：

PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

bash:

```bash
python -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m pytest -q
```

安装时会下载 `pyproject.toml` 中声明的运行依赖和开发测试依赖。

## Bash 启动后端

如果你不熟悉 Python 环境，可以直接使用脚本初始化依赖并运行当前后台检查入口：

```bash
bash scripts/start-backend.sh
```

脚本会自动创建 `.venv`、安装 `pyproject.toml` 中声明的依赖，并在未设置 `QT_DATABASE_PATH` 时使用 `data/quant_trading.db`。当前首版后台入口是 `qt service check`，用于检查配置、数据库和台账读取路径。

设置本地 SQLite 路径：

PowerShell:

```powershell
$env:QT_DATABASE_PATH = "data/quant_trading.db"
```

bash:

```bash
export QT_DATABASE_PATH=data/quant_trading.db
```

新增持仓：

```bash
qt ledger add --symbol 600000 --name 浦发银行 --quantity 1000 --available-quantity 1000 --cost-price 9.50 --opened-at 2026-07-06
```

查看持仓：

```bash
qt ledger list
```

维护自选置顶观察池：

```bash
qt watchlist add --symbol 600519 --name 示例白酒 --rank 1 --plan-enabled true --note 核心自选
qt watchlist list
qt watchlist export
```

## 资金账户与账户估值

手动资金账户是现金余额、净本金和可用买入资金的权威来源。模拟银证转入、银证转出和现金校准都需要通过 CLI 记录，后台服务只读取资金账户并生成账户估值快照。

初始化资金账户：

```bash
qt cash init --cash 50000 --note 初始本金
```

模拟银证转账和现金校准：

```bash
qt cash transfer-in --amount 10000 --note 银证转入
qt cash transfer-out --amount 5000 --note 银证转出
qt cash adjust --cash 48000 --note 手动校准券商可用资金
```

查看资金账户，并通过统一盘中工作流刷新账户估值：

```bash
qt cash show
qt workflow intraday
```

`qt account snapshot` 已退役并以非零状态退出；账户快照只由统一盘中工作流生成，Web 和 API 保留只读查询。

后台服务检查：

```bash
qt service check
```

统一 HTTP API 服务启动：

```bash
qt service run
```

服务默认监听 `0.0.0.0:8000`，便于用户要求的局域网访问，但这不等于可以直接暴露到公网。本项目不自带 TLS、反向代理或防火墙；只需本机访问时应改为：

```bash
export QT_API_HOST=127.0.0.1
qt service run
```

默认调度使用 `exchange-calendars` 的 `XSHG` 交易日历和 `Asia/Shanghai` 时区：

- 盘中 `09:30-11:30`、`13:00-15:00` 每 3 分钟运行一次统一工作流。
- 收盘 `15:15` 首次检查当日数据，未就绪每 5 分钟重试，最晚 `16:30`。
- 原始分钟线在交易日 `16:35` 独立清理。
- 日 K/资金流 backfill 不自动调度，由认证 HTTP 或 CLI 按需运行。
- 邮件 outbox worker 每 15 秒轮询，不受交易时段限制。

四种工作流类型 `intraday/close/backfill/cleanup` 全部由同一个 `DecisionWorkflow` 执行，每种类型最多一个实例，错过的盘中触发只合并为当前有效周期。服务在交易日 `15:15-16:30` 内重启且当日计划未发布时会立即恢复收盘就绪检查，此规则不受可选的 `QT_SERVICE_RUN_ON_START_WHEN_SCHEDULER_ENABLED` 普通启动开关限制。超过 `16:30` 的收盘补跑必须通过认证入口显式执行并留下审计记录。

运行统一收盘工作流、查看计划和建议：

```bash
qt workflow close --date 2026-07-10
qt plan latest --json
qt recommendations list --json
qt service status --json
```

旧 `qt recommendations scan` 已退役并以非零状态退出，旧 `POST /api/v1/recommendations/scan` 在认证后固定返回 HTTP `410 recommendation_scan_retired`。手动生成盘中建议使用 `qt workflow intraday` 或 `POST /api/v1/service/workflows/intraday/run`，不会形成第二条建议写入路径。

建议 API 和 CLI 只生成可解释的本地建议。`buy/add` 需要活动收盘计划、至少两个独立有效因子和硬性风控；计划外非持仓机会只能 `watch/avoid`。持仓新风险可以覆盖计划，但数据不足时必须降级并保留风险、失效条件和人工复核要求。

## 行情数据和市场输入快照

手动采集一次可追溯市场输入快照：

```bash
qt market snapshot
```

重数据只覆盖手动持仓和 `plan_enabled=true` 的非持仓自选；盘中进一步限制为当前持仓和活动计划内标的。重复标的只采集一次，provider 返回的额外股票不会扩大股票池。

系统为启用标的维护最近 250 个 `XSHG` 交易日的前复权日 K、60 个交易日的主力/超大单/大单/中单/小单净额和净占比，以及当前交易日 1 分钟行情。日 K 和资金流使用追加版本及不可变快照；原始分钟线只保留最近 20 个交易日，但分时强弱、规则版本、建议输入和审计引用长期保留。

盘中报价和分钟数据默认落后超过 6 个有效交易分钟即标记 stale，午休和非交易时段不累计。可在 `.env` 中设置 `QT_MARKET_STALE_TRADING_MINUTES=6`（允许 1 到 60）；实际生效值会保存到输入快照并在数据引用页展示。

公开报价响应若没有经过契约验证的市场源时间，只保存抓取时间并标为 partial，且不得用抓取时间替代 `data_time`。收盘工作流只有在报价最新价与同日已固化前复权日 K 收盘价严格一致时，才以交易所会话收盘时间创建一条 partial 验证报价；否则不能发布收盘计划、放行 `buy/add` 或形成完整账户估值。分时规则版本和九个阈值可通过 `QT_MARKET_STRENGTH_*` 环境变量配置，实际值随强弱快照保存。

每个 `DecisionWorkflow` 运行形成 `run_id -> market_input_snapshot_id -> plan_id -> recommendation_id -> notification_id -> delivery_id` 追踪链；数据库对每种工作流类型维持全局 running 租约，避免 HTTP、CLI 和调度器跨周期重复采集。逐标的外部失败保存为 degraded/failed/stale 质量结果并继续其他标的；数据库、引用或模型契约失败终止当前整轮。CLI 和 API 不输出第三方原始响应。

工作流 CLI 范围包括日 K/资金流基线回填、收盘或盘中手动运行、指定交易日补跑、工作流与数据质量查询、分钟线清理、通知读取和邮件失败重试。维护和工作流命令支持人类可读摘要及 `--json`；两者包含一致的运行 ID、状态、质量和成本指标。强制运行、窗口外盘中运行、跳过日历或晚于截止补跑必须提供 `--reason`，CLI 会通过隐藏输入提示校验本地 API 访问密码，密码不会进入命令参数、输出或审计。具体已安装命令以 `qt --help` 及子命令 `--help` 为准，所有入口共享相同 repository、adapter 和 service。

```bash
qt market backfill --date 2026-07-13
qt market cleanup --date 2026-07-13
qt market runs --limit 20
qt market runs --limit 20 --json
qt workflow intraday
qt workflow close --date 2026-07-13
qt workflow close --date 2026-07-13 --force --reason "人工确认补跑"
```

监控页和 `/api/v1/market/runs` 展示工作流类型、交易日、周期、幂等键、起止与耗时、请求/处理标的数、provider 调用与耗时、行数、计划/建议/通知/outbox 数量，以及各数据集 `complete/degraded/failed/stale` 的 `dataset_counts`。服务状态另显示最近任务原因及累计 `overrun_count/skipped_count`。计划、建议、通知、反馈、审计和邮件投递等列表 API 统一返回 `{items,total,page,page_size}`。

## 数据源密钥安全

东方财富和妙想相关 API key、token、cookie、账号信息不得提交到 git。`MX_APIKEY` 只能来自环境变量或未入库本地配置。前端数据源设置页和 `/api/v1/datasource/eastmoney/key` 只维护本地凭据状态，接口响应不返回密钥明文。

## SMTP 和邮件 outbox

SMTP 可以从 Web 设置页配置，也可以通过 CLI 查看脱敏状态、发送测试邮件、查询失败投递和显式重试：

```bash
qt email status
qt email test
qt email deliveries --status dead
qt email retry <delivery_id>
```

SMTP 密码经用户确认后明文保存在本地 SQLite，这是项目唯一的秘密存储例外。读取 API、CLI、Web 回填、日志、审计、错误和 outbox 永远不得包含原值；密码输入留空保留已有值，替换和清除必须是明确操作。数据库导出和备份会包含 SMTP 明文密码，因此 `data/`、备份和本地运行数据必须保持 git 忽略并限制为本机用户可读。

`buy/add/sell/reduce` 在本地通知成功后立即进入 outbox，`hold/watch/avoid` 只进入收盘每日摘要。关键工作流故障先写数据库/Web 通知、控制台和 JSONL，再在 SMTP 可用时进入邮件 outbox。投递失败按 1、5、15、30、60 分钟退避，达到最多 6 次尝试后成为 `dead`，同时生成去重的数据库/Web、控制台和 JSONL 本地告警。邮件失败不会回滚建议，也不会重跑决策工作流；SMTP 禁用、未配置或自身故障不能压制本地告警。

## 本地前端控制台开发

前端控制台使用 Node 24 和 pnpm。推荐通过 nvm 切换 Node 版本：

```bash
nvm use
pnpm -C src/web install
pnpm -C src/web test
pnpm -C src/web build
pnpm -C src/web e2e
pnpm -C src/web dev
```

如果本机尚未安装 Node 24：

```bash
nvm install 24
nvm use
```

前端开发服务会通过 Vite 将 `/api` 请求代理到本地后端 `http://127.0.0.1:8000`。先启动后端：

```bash
qt service run
```

前端包含台账、资金账户、账户快照、自选置顶、建议、通知、复盘、调度和设置页，并提供顶级“行情”工作台。行情页在桌面使用左侧决策标的扫描器，在小屏幕使用选择抽屉；详情包含 `概览`、`K 线`、`资金流`、`分时强弱` 和 `数据引用`。图表使用后端返回的前复权日 K、均线、完整资金流、分钟价格、前收、VWAP、成交量和建议发生点，不在浏览器计算特征或动作。

页面必须明确区分 loading、empty、partial/degraded、stale、failed 和认证过期状态。SMTP 配置与失败投递列表分别降级，邮件故障不能影响本地通知。前端不自动真实下单，不控制真实交易客户端，不保存真实券商凭据。

人工执行反馈只写入复盘记录和关联通知状态，不修改手动持仓台账、手动资金账户、现金余额或净本金。真实成交后的权威数据仍需要用户手动维护。

`qt service debug-run` 已退役，不再启动独立的账户行情轮询。单次执行使用 `qt workflow intraday`，常驻运行使用 `qt service run`。

## Docker 示例

构建镜像并运行只读服务检查：

```bash
docker compose build
docker compose run --rm qt qt service check
```

执行台账命令：

```bash
docker compose run --rm qt qt ledger list
```

`compose.yaml` 会将本地 `./data` 挂载到容器内 `/app/data`，默认数据库路径为 `/app/data/quant_trading.db`。不要把真实 API key、token、cookie 或账号信息写入镜像、compose 文件或 git。
