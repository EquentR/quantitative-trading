# quantitative-trading

个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不会自动下单，不控制真实交易客户端，不绕过人工确认。

## 手动持仓台账

首版以手动持仓台账作为真实持仓、成本价、持仓数量和可用数量的唯一权威源。

## 自选置顶观察池

自选置顶观察池是候选买入股票池来源，不代表真实持仓。观察池记录 `symbol,name,rank,plan_enabled,note`，并由系统记录来源：

- `manual`：本地手动维护。
- `synced`：外部自选置顶同步结果，默认不进入计划。
- `manual_synced`：外部同步命中本地手动记录，保留本地 `plan_enabled` 和备注。

非持仓自选股票只有 `plan_enabled=true` 时才会进入收盘计划和建议扫描；持仓台账中的股票始终按持仓来源进入计划。

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

查看资金账户和账户估值：

```bash
qt cash show
qt account snapshot
```

后台服务检查：

```bash
qt service check
```

统一 HTTP API 服务启动：

```bash
qt service run
```

默认调度使用 `Asia/Shanghai` 时区：账户快照每 180 秒执行一次，收盘计划在周一至周五 15:30 生成下一工作日计划，盘中触发在 09:35-11:30、13:00-13:59 和 14:00-14:55 扫描最新有效计划。当前只跳过周末，不内置 A 股节假日交易日历。

生成计划和建议：

```bash
qt plan generate --date 2026-07-10
qt plan latest
qt recommendations scan
qt recommendations list
```

建议 API 和 CLI 只生成可解释的本地建议；未接入实时行情时会输出保守持有或观察建议，并保留风险和失效条件。

## 数据源密钥安全

东方财富和妙想相关 API key、token、cookie、账号信息不得提交到 git。`MX_APIKEY` 只能来自环境变量或未入库本地配置。前端数据源设置页和 `/api/v1/datasource/eastmoney/key` 只维护本地凭据状态，接口响应不返回密钥明文。

## 本地前端控制台开发

前端控制台使用 Node 24 和 pnpm。推荐通过 nvm 切换 Node 版本：

```bash
nvm use
pnpm -C src/web install
pnpm -C src/web test
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

前端只维护本地手动台账、资金账户、账户快照、自选置顶观察池、建议复盘反馈和调度状态，不自动真实下单，不控制真实交易客户端，不保存真实券商凭据。

人工执行反馈只写入复盘记录和关联通知状态，不修改手动持仓台账、手动资金账户、现金余额或净本金。真实成交后的权威数据仍需要用户手动维护。

调试版后台服务单次快照：

```bash
qt service debug-run --once
```

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
