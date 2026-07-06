# quantitative-trading

个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不会自动下单，不控制真实交易客户端，不绕过人工确认。

## 手动持仓台账

首版以手动持仓台账作为真实持仓、成本价、持仓数量和可用数量的唯一权威源。

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
