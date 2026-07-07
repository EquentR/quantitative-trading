# 后台账户估值服务设计

## 背景

本项目是个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不自动真实下单，不控制真实交易客户端，不读取真实交易凭据，也不得把系统输出描述为确定性收益或保证正收益。

当前仓库已经实现手动持仓台账、SQLite 存储、`qt ledger` CLI 和 `qt service check`。下一阶段需要实现真正可运行的后台调试服务，并补齐资金口径，使系统能够基于手动持仓台账、手动资金账户和 AkShare 行情计算账户盈亏、仓位比例和可用买入资金。

## 目标

- 新增资金账户台账，记录现金余额、累计银证转入、累计银证转出、净本金和资金流水。
- 支持模拟银证转入、模拟银证转出、现金校准和资金查询。
- 使用 AkShare adapter 获取持仓行情，并转换为项目内部行情模型。
- 生成账户估值快照，包括持仓市值、浮动盈亏、总资产、总收益率、仓位比例和可用买入现金。
- 实现 `qt service run` 调试版后台常驻服务，启动后按交易时间周期生成账户快照。
- CLI、后台服务和未来 API 共享同一套 application service 和 Pydantic 输出模型。
- 在结构上预留未来 FastAPI 或其他前端 API 入口，但本切片不实现前端页面和 HTTP API。

## 非目标

- 不实现自动真实下单。
- 不模拟点击真实交易客户端。
- 不读取、保存或提交真实账户密码、token、cookie、API key。
- 不从东方财富模拟组合或 AkShare 推断真实持仓成本、数量或可用数量。
- 不记录真实买卖成交流水，也不根据买卖流水自动修改现金或持仓。
- 不实现完整策略信号、推荐生成、远程推送或前端页面。
- 不实现 Windows Service、systemd 安装或生产级进程守护。

## 推荐方案

采用“账户估值核心服务 + 调试版常驻调度器”方案。

账户估值核心服务负责读取手动持仓、资金账户和行情快照，并返回 API 友好的 Pydantic 模型。CLI 只负责格式化输出，后台 runner 只负责调度和日志，未来 API 入口可以直接复用同一套 service。

不直接上 FastAPI 的原因是当前需求重点是资金口径、行情估值和后台调试运行。提前引入 HTTP API 会带来接口版本、鉴权、服务生命周期和前端契约等额外范围。只要本切片保持 service 层无 Typer 依赖、输出模型可序列化，后续加 API 不需要重写业务逻辑。

## 模块边界

新增或扩展模块按以下职责设计：

- `cash`：资金账户模型、资金流水、repository 和 service。
- `market`：行情 adapter，首个实现为 AkShare provider。
- `account`：账户估值 service，合并持仓、资金和行情，生成账户快照。
- `runtime` 或 `service`：调试版后台 runner 和交易时间调度。
- `cli`：薄入口，调用 `cash`、`account` 和后台 runner。
- `storage`：SQLite migration 增加资金账户、资金流水和账户快照相关表。

后台服务不得调用持仓台账或资金账户的写入方法。资金和持仓的人工维护仍通过 CLI 完成。后台只读取台账、拉取行情、计算快照、输出日志。

## 资金数据模型

资金账户分为当前状态和流水两层。

### `cash_account`

当前状态表只保留一行账户状态：

```sql
CREATE TABLE cash_account (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  cash_balance REAL NOT NULL CHECK (cash_balance >= 0),
  total_transfer_in REAL NOT NULL CHECK (total_transfer_in >= 0),
  total_transfer_out REAL NOT NULL CHECK (total_transfer_out >= 0),
  updated_at TEXT NOT NULL,
  CHECK (total_transfer_in >= total_transfer_out)
);
```

首版不存储 `net_principal` 权威字段，查询时按 `total_transfer_in - total_transfer_out` 派生，避免本金口径出现双写不一致。

### `cash_transactions`

每次资金操作都记录流水：

```sql
CREATE TABLE cash_transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  amount REAL NOT NULL CHECK (amount > 0),
  cash_before REAL NOT NULL CHECK (cash_before >= 0),
  cash_after REAL NOT NULL CHECK (cash_after >= 0),
  occurred_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  CHECK (type IN ('initial_deposit', 'transfer_in', 'transfer_out', 'cash_adjustment'))
);
```

资金操作口径：

- `init`：只能在未初始化时执行，等价于一笔 `initial_deposit`，计入累计转入和净本金。
- `transfer-in`：现金增加，累计转入增加，净本金增加。
- `transfer-out`：现金减少，累计转出增加，净本金减少；不能超过当前现金，也不能导致净本金小于 0。
- `adjust`：直接校准现金余额，只修改现金，不修改累计转入、累计转出和净本金；必须记录备注，流水 `amount` 记录调整前后现金差额的绝对值。
- 后台服务只读资金账户，不自动修改现金。
- 本阶段不记录买卖成交流水，也不根据建议、持仓变化或行情变化修改现金。

## CLI 命令

新增命令组为 `qt cash` 和 `qt account`，并扩展 `qt service`。

```text
qt cash init --cash 50000 --note 初始本金
qt cash show
qt cash transfer-in --amount 10000 --note 银证转入
qt cash transfer-out --amount 5000 --note 银证转出
qt cash adjust --cash 48000 --note 手动校准券商可用资金
qt cash transactions
qt account snapshot
qt service check
qt service run
```

CLI 输出要求：

- 金额默认保留 2 位小数。
- 收益率默认以百分比展示并保留 2 位小数。
- `cash show` 和 `account snapshot` 应支持 `--json`，便于测试和未来 API DTO 对齐。
- 错误信息必须明确区分未初始化资金账户、转出超过现金、行情不可用、持仓为空和数据库不可用。

## AkShare 行情模型

外部行情必须通过 adapter 接入。账户估值 service 不得直接调用 AkShare，也不得依赖 AkShare 原始字段名。

内部行情模型为 `QuoteSnapshot`：

- `symbol`
- `name`
- `current_price`
- `change_pct`
- `data_time`
- `fetched_at`
- `source`
- `status`
- `warning`

`status` 可选值：

- `ok`：行情可用于估值。
- `partial`：部分字段缺失，但价格可用于估值。
- `failed`：该标的行情获取失败。
- `stale`：行情过期，不应用于新的买入资金建议。

AkShare 数据可能存在延迟、字段变化、接口不可用或限频。adapter 必须显式返回状态和错误说明，不能让异常数据静默进入账户估值。

## 账户快照模型

账户估值 service 读取资金状态、手动持仓台账和行情 adapter，返回 `AccountSnapshot`。

字段：

- `cash_balance`
- `net_principal`
- `market_value`
- `position_cost`
- `floating_pnl`
- `floating_pnl_pct`
- `total_assets`
- `total_pnl`
- `total_pnl_pct`
- `position_ratio`
- `available_buying_cash`
- `positions`
- `status`
- `warnings`
- `created_at`

每个持仓估值项包含：

- `symbol`
- `name`
- `quantity`
- `available_quantity`
- `cost_price`
- `position_cost`
- `current_price`
- `market_value`
- `floating_pnl`
- `floating_pnl_pct`
- `ledger_updated_at`
- `quote_data_time`
- `quote_fetched_at`
- `status`
- `warning`

计算公式：

- `position_cost = quantity * cost_price`
- `market_value = quantity * current_price`
- `floating_pnl = market_value - position_cost`
- `floating_pnl_pct = floating_pnl / position_cost`
- `total_assets = cash_balance + market_value`
- `total_pnl = total_assets - net_principal`
- `total_pnl_pct = total_pnl / net_principal`
- `position_ratio = market_value / total_assets`
- `available_buying_cash = cash_balance`

若分母为 0，相关百分比字段应显式为空或不可计算，不能伪造为 0。

账户状态可选值：

- `ok`：资金账户已初始化，行情可用于全部持仓估值。
- `partial`：部分持仓行情失败或过期。
- `market_data_unavailable`：整批行情不可用。
- `cash_not_initialized`：资金账户未初始化。

行情失败口径：

- 单票失败时，该持仓仍出现在 `positions` 中，但市值和盈亏标记为不可计算，账户状态为 `partial`。
- 整批失败时，不生成新的有效估值，账户状态为 `market_data_unavailable`。
- 若存在上一轮快照，可以在日志或输出中引用上一轮快照，但不能把上一轮价格当作本轮结果。
- 行情过期时状态降级为 `stale`，不用于买入资金建议。

## 后台调试服务

`qt service run` 是首版真正后台服务入口，但运行形态定位为调试版前台常驻进程。

启动行为：

- 加载配置并执行 SQLite migration。
- 检查手动持仓台账是否可读。
- 检查资金账户是否初始化。
- 检查 AkShare provider 是否可用。
- 立即执行一次账户快照。
- 控制台输出摘要，结构化日志写入完整 JSON。

周期任务：

- `account_snapshot_opening`：启动时或开盘前后执行一次。
- `account_snapshot_intraday`：交易时段内按配置间隔执行，默认 180 秒。
- `account_snapshot_close`：收盘后执行一次，用于当日资产快照。

调试版约束：

- 前台常驻，支持 `Ctrl+C` 正常退出。
- 可使用 APScheduler 或同类成熟调度器。
- 非交易时段不做高频轮询。
- 不自动生成买卖建议。
- 不自动修改持仓台账。
- 不自动修改资金账户。
- 不做 Windows Service、systemd 安装。
- Docker 和 compose 后续可以直接运行同一个命令。

## 配置

新增或确认以下配置项：

- `QT_DATABASE_PATH`
- `QT_LOG_DIR`
- `QT_MARKET_PROVIDER=akshare`
- `QT_INTRADAY_INTERVAL_SECONDS=180`
- `QT_TIMEZONE=Asia/Shanghai`
- `QT_ENABLE_MARKET_FETCH=true`

交易日和交易时间判断统一使用 `Asia/Shanghai`。机器本地时区不作为 A 股交易时间判断依据。

## 结构化日志和快照持久化

本切片必须写结构化日志，并持久化账户快照，记录：

- 账户快照 ID 或日志引用。
- 快照创建时间。
- 资金账户更新时间。
- 使用的持仓台账更新时间。
- 行情数据时间和拉取时间。
- 每只持仓的估值状态。
- 账户汇总字段。
- 警告和错误信息。

账户快照持久化表为 `account_snapshots`：

```sql
CREATE TABLE account_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  cash_account_updated_at TEXT,
  ledger_max_updated_at TEXT,
  market_value REAL,
  total_assets REAL,
  total_pnl REAL,
  position_ratio REAL,
  payload_json TEXT NOT NULL
);
```

`payload_json` 存储内部 `AccountSnapshot` JSON，便于后续复盘和 API 查询。持久化内容不得包含 AkShare 原始字段名或第三方原始响应。

## API 预留

本切片不实现 HTTP API，但必须为未来 API 保留边界：

- service 层不依赖 Typer、控制台或后台 runner。
- service 返回 Pydantic 模型。
- CLI 格式化逻辑独立于核心计算。
- 后台 runner 调用 `AccountService.create_snapshot()`，不直接拼装快照。
- 错误状态通过模型和领域异常表达，便于未来转换为 HTTP 状态码。
- `cash show` 和 `account snapshot` 的 JSON 输出应与未来 API 响应模型保持一致。

## 错误处理

资金相关错误：

- 未初始化资金账户时，`cash show` 和 `account snapshot` 应给出明确提示。
- 重复初始化应失败。
- 转出金额超过当前现金应失败。
- 转出导致净本金小于 0 应失败。
- 现金调整必须记录备注。

行情相关错误：

- 单票行情失败不影响其他持仓估值。
- 整批行情失败时账户快照状态为 `market_data_unavailable`。
- 行情过期时账户快照或持仓估值状态应降级。
- 所有行情异常都应进入结构化日志。

后台相关错误：

- 后台启动时配置错误应立即失败并输出可读原因。
- 周期任务中的行情错误不应导致进程退出。
- 数据库不可写或日志目录不可写应清晰报错。

## 测试要求

实现阶段应按 TDD 或至少可重复验证步骤覆盖：

- 资金模型接受合法输入并拒绝非法金额。
- `init` 只能执行一次，并计入累计转入和净本金。
- `transfer-in` 正确增加现金、累计转入和净本金。
- `transfer-out` 正确减少现金、增加累计转出，并拒绝超额转出。
- `adjust` 只修改现金，不修改累计转入、累计转出和净本金。
- 资金流水记录 `cash_before`、`cash_after`、类型、时间和备注。
- `AccountSnapshot` 公式计算正确。
- 空持仓、未初始化资金账户、净本金为 0 的场景有明确状态。
- fake market provider 下的单票失败、整批失败和 stale 数据处理正确。
- AkShare adapter 单独测试字段映射和异常包装，不让真实网络成为核心单元测试依赖。
- `qt cash`、`qt account snapshot` 和 `qt service check` CLI 行为可测试。
- `qt service run` 的业务 runner 可用可控调度器或单步执行方式测试，不让测试进程真的常驻。

## 文档影响

本切片会改变资金、仓位和账户估值口径。实现时需要同步更新：

- `docs/project-spec.md`：补充资金账户、账户估值和后台调试服务范围。
- `docs/trading-policy.md`：补充买入资金约束依赖现金余额和净本金口径。
- `docs/data-sources.md`：补充手动资金账户作为账户资金权威源，并明确 AkShare 只提供行情。
- `docs/recommendation-contract.md`：后续推荐涉及仓位约束时应引用账户资金上下文。

本设计文档本身不修改上述规约语义，实施代码时必须同批更新相关 docs。
