# quantitative-trading

个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不会自动下单，不控制真实交易客户端，不绕过人工确认。

## 手动持仓台账

首版以手动持仓台账作为真实持仓、成本价、持仓数量和可用数量的唯一权威源。

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
