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

安装时会下载运行依赖 `pydantic`、`typer`，以及开发测试依赖 `pytest`。

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

后台服务检查：

```bash
qt service check
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
