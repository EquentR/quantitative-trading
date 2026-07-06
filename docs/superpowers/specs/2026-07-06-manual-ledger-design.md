# 手动持仓台账 SQLite 设计

## 背景

本项目是个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不自动真实下单，不控制真实交易客户端，不读取真实交易凭据，也不得把系统输出描述为确定性收益或保证正收益。

首个实现切片选择“手动持仓台账闭环”。手动持仓台账是后续股票池、策略、风控、推荐、通知和审计日志共同依赖的真实持仓权威源。真实持仓、成本价、持仓数量和可用数量都必须来自这套台账。

## 目标

- 建立 Python 项目基础结构。
- 使用本地 SQLite 数据库存储当前真实持仓台账。
- 提供 CLI 和后台服务共享的台账 service/repository。
- 通过 CLI 支持新增、更新、删除、导入、查看和导出台账。
- 后台服务只能读取真实持仓台账，不能自动修改台账。
- 在台账数据进入后续策略和风控前完成校验。
- 保留推荐消息契约需要的台账更新时间。

## 非目标

- 不实现自动真实下单。
- 不模拟点击真实交易客户端。
- 不读取、保存或提交真实账户密码、token、cookie、API key。
- 不在本切片实现正式策略信号生成。
- 不在本切片接入 AkShare 或东方财富 adapter。
- 不在本切片实现完整审计日志或完整推荐生成。

## 推荐方案

台账主存储直接使用 SQLite，而不是先用 JSON 文件。原因是台账后续很可能需要快照、审计引用、人工反馈、导入记录和复盘查询。SQLite 可以承接这些演进，不需要后续再从文件格式迁移一次。

迁移机制先采用项目内轻量 SQL migration，不立即引入 Alembic。当前只有本地单库和少量表，完整迁移框架暂时偏重。只要 repository 和 service 边界保持稳定，后续接入 Alembic 不会影响 CLI、后台服务和核心业务逻辑。

## 架构

第一版按职责拆成几个小模块：

- `models`：pydantic 模型和枚举，负责台账记录和命令输入校验。
- `storage`：SQLite 连接管理和 migration 执行。
- `ledger`：台账 repository 和 service，负责读写当前持仓。
- `cli`：Typer CLI 命令，只调用 ledger service。
- `service`：后台服务入口，负责检查和读取台账，但不修改台账。

CLI 和后台服务必须共享同一套 repository/service。CLI 使用可读写的 ledger service；后台服务只使用只读接口或只调用读取方法。

## 数据模型

首版只建一张当前持仓表：`positions`。

```sql
CREATE TABLE positions (
  symbol TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity >= 0),
  available_quantity INTEGER NOT NULL CHECK (available_quantity >= 0),
  cost_price REAL NOT NULL CHECK (cost_price > 0),
  opened_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  CHECK (available_quantity <= quantity)
);
```

字段规则：

- `symbol`：首版使用 6 位 A 股代码，例如 `600000`。
- `name`：股票名称，不能为空。
- `quantity`：当前手动记录的持仓数量，必须大于等于 0。
- `available_quantity`：当前可用数量，必须大于等于 0，且不能超过 `quantity`。
- `cost_price`：手动记录的成本价，必须大于 0。
- `opened_at`：建仓日期，使用 ISO 日期格式。
- `updated_at`：最近更新时间，系统在每次变更时写入带时区的 ISO 时间。
- `note`：备注，可为空；存储时统一使用空字符串表示无备注。

后续可以在不推翻本切片 API 的前提下继续增加 `position_events`、`ledger_snapshots`、`audit_logs`、`manual_feedback` 等表。

## CLI 命令

首版 CLI 命令组为 `qt ledger`。

```text
qt ledger list
qt ledger add --symbol 600000 --name 浦发银行 --quantity 1000 --available-quantity 1000 --cost-price 9.50 --opened-at 2026-07-06
qt ledger update 600000 --quantity 1200 --available-quantity 1000 --cost-price 9.40 --note 手动调整
qt ledger remove 600000
qt ledger import positions.csv
qt ledger export
qt service check
```

导入 CSV 字段为：

```text
symbol,name,quantity,available_quantity,cost_price,opened_at,note
```

导入时必须先校验所有行。如果任意一行不合法，整次导入失败，不能出现只写入部分行的情况。

## 后台服务约束

首个后台命令为 `qt service check`。它用于检查配置是否可加载、SQLite 数据库是否可打开、migration 是否可执行、当前持仓是否可读取。

后台服务不能调用台账写入方法。实现时通过只读 service 接口约束，并在测试中覆盖这一点。

## 错误处理

CLI 输出的校验错误应清晰、可理解。存储错误需要保留操作上下文，方便定位是哪个动作失败，但不能暴露任何敏感信息。

新增已存在的 `symbol` 时应失败并给出明确提示。更新或删除不存在的 `symbol` 时也应失败并给出明确提示。CSV 导入中只要存在非法数据，就必须在写入前整体失败。

## 测试要求

本切片按 TDD 实现。每个核心行为先写测试，确认测试因功能缺失而失败，再写最小实现让它通过。

必须覆盖：

- 模型校验接受完整合法持仓。
- 模型校验拒绝非法股票代码、负数持仓、可用数量大于持仓数量、空名称、非正成本价。
- SQLite migration 能创建 `positions` 表。
- repository 能正确新增、更新、删除、读取和列出持仓。
- CSV 导入在存在非法行时具备原子性，不会写入部分数据。
- CLI 命令通过共享 ledger service 完成操作，并输出有用结果。
- 后台服务检查命令可以读取台账，但没有写入路径。

## 文档影响

本设计是在实现现有项目规约，不改变策略、风控、数据源和推荐消息契约。因此本切片不需要修改 `docs/project-spec.md`、`docs/trading-policy.md`、`docs/data-sources.md` 或 `docs/recommendation-contract.md` 的语义内容。

如果后续实现改变台账字段，或改变推荐消息中可见的持仓上下文，必须在同一次变更中同步更新 `docs/data-sources.md` 和 `docs/recommendation-contract.md`。
