# Windows CSV 与可追溯行情快照设计

## 背景

当前主干在 Windows 上运行完整 Python 测试时，持仓和自选置顶 CSV 上传接口会稳定触发 `PermissionError`。两个接口都在 `NamedTemporaryFile` 仍保持打开时，将临时文件路径交给 repository 再次打开；Windows 不允许这种文件共享方式。

仓库已经具备 `QuoteSnapshot` 行情内部契约、股票池快照表和 `market_input_snapshots` 表，但缺少行情快照表、聚合市场输入模型、repository、采集工作流和稳定操作入口。因此系统无法保存“当时使用了哪些报价”，也无法把市场数据发生时间与系统获取时间一起用于后续复盘。

本设计只处理两项工作：

1. 修复 Windows CSV 上传。
2. 落地决策启用股票的实时或准实时报价快照及聚合引用。

历史 K 线、资金流、分时强弱、策略消费、收盘计划消费、通知和调度集成本期不实现。

## 规约边界

实现必须继续遵守：

- `docs/project-spec.md`
- `docs/trading-policy.md`
- `docs/data-sources.md`
- `docs/recommendation-contract.md`

市场数据只通过 `MarketDataProvider` adapter 边界进入核心逻辑。快照不得包含 AkShare、东方财富或腾讯的原始字段，不得包含 API key、token、cookie、真实券商凭据或真实账户认证信息。

## Windows CSV 修复

### 根因

`import_positions_csv` 和 `import_pinned_csv` 使用 `NamedTemporaryFile(delete=True)`，在临时文件上下文仍打开时调用 service。service 再通过 `Path.open()` 打开同一路径。该方式在部分 Unix 环境可工作，但在 Windows 上会因为文件仍被第一个句柄占用而失败。

### 修复方式

两个 API 上传入口统一采用以下生命周期：

1. 读取 `UploadFile` 字节。
2. 创建不会在关闭时自动删除的临时 `.csv` 文件。
3. 写入并关闭临时文件。
4. 将已关闭文件的路径传给现有 service/repository。
5. 在 `finally` 中删除临时文件。

CLI、service 和 repository 的 `Path` 输入契约保持不变。CSV 内容校验、全量替换事务和错误响应语义不变。即使解析、校验或数据库写入失败，临时文件也必须被删除。

## 行情快照模型

### QuoteSnapshot

继续复用 `quantitative_trading.market.models.QuoteSnapshot`。每个请求标的必须对应一条持久化报价记录，包括失败和过期状态。

### MarketInputSnapshot

新增聚合模型 `MarketInputSnapshot`：

- `universe_snapshot_id: int`：本次采集使用的股票池快照 ID。
- `quote_snapshot_refs: dict[str, int]`：股票代码到报价快照 ID 的映射。
- `history_snapshot_refs: dict[str, int]`：本期固定为空映射。
- `money_flow_snapshot_refs: dict[str, int]`：本期固定为空映射。
- `intraday_strength_snapshot_refs: dict[str, int]`：本期固定为空映射。
- `data_time: datetime | None`：所有具有市场时间的报价中最早的时间；全部失败时为 `None`。
- `fetched_at: datetime`：聚合采集工作流的系统时间。
- `warnings: list[str]`：失败、过期、部分数据、provider 漏返回和未实现重数据类型的说明。

所有 datetime 必须包含时区。使用最早报价时间作为聚合 `data_time`，避免较新的单条报价掩盖同批次中更旧的数据。

## 存储设计

### quote_snapshots

新增 SQLite 表：

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `symbol TEXT NOT NULL`
- `status TEXT NOT NULL`
- `data_time TEXT`
- `fetched_at TEXT NOT NULL`
- `source TEXT NOT NULL`
- `payload_json TEXT NOT NULL`

`payload_json` 保存内部 `QuoteSnapshot` JSON。索引覆盖 `symbol, id`，便于后续按股票读取历史报价。repository 提供 `save`、`get` 和按 symbol 读取最新记录的能力。

### market_input_snapshots

复用现有表结构。新增 repository 提供：

- `save(snapshot) -> int`
- `get(snapshot_id) -> MarketInputSnapshot | None`
- `latest() -> MarketInputSnapshot | None`

保存聚合快照和全部报价快照时使用同一数据库连接。采集工作流控制事务边界，确保不会出现聚合快照引用未提交报价的情况。

## 采集工作流

新增共享 `MarketSnapshotService`，依赖：

- SQLite connection。
- `MarketDataProvider`。
- 可注入的当前时间。

工作流如下：

1. 读取手动持仓台账和本地自选置顶镜像。
2. 使用现有 `build_universe` 生成并保存股票池快照。
3. 选择所有持仓股票和 `plan_enabled=true` 的自选股票。
4. 按股票代码稳定排序后调用一次 `MarketDataProvider.get_quotes()`。
5. 对 provider 返回的每条内部 `QuoteSnapshot` 做符号匹配校验并持久化。
6. 对 provider 漏返回的请求标的生成 `status=failed`、`source=market_snapshot_service` 的失败快照，warning 说明 provider 未返回该股票。
7. 忽略 provider 返回的非请求标的，并在聚合 warnings 中记录，避免外部数据扩大股票池。
8. 保存聚合 `MarketInputSnapshot`，其报价引用覆盖每个请求标的。

当决策启用集合为空时，不调用 provider，仍保存空的可追溯聚合快照，并写入“无决策启用标的” warning。

provider 整体抛出异常时，为每个请求标的生成失败快照。异常摘要必须经过现有 sanitization 处理，不能泄露密钥、凭据或本地敏感路径。

## 操作入口

### CLI

新增 `qt market snapshot`：

- 使用与账户估值一致的市场 provider 配置。
- 输出聚合快照 ID、股票池快照 ID、请求标的数量、成功/部分/过期/失败数量、数据时间和 warnings。
- 不输出第三方原始响应。

### HTTP API

新增认证后接口：

- `POST /api/v1/market/snapshots`：执行一次采集并返回快照 ID 和聚合快照。
- `GET /api/v1/market/snapshots/latest`：返回最新聚合快照；不存在时返回统一 `market_snapshot_not_found` 错误。
- `GET /api/v1/market/snapshots/{snapshot_id}`：按 ID 查询聚合快照；不存在时返回相同错误码并携带 ID。

API、CLI 和后续调度入口只能调用共享 `MarketSnapshotService`，不得各自实现行情解释逻辑。

## 错误处理

- CSV 解析或字段校验继续返回 `validation_error`，不得将临时路径暴露给客户端。
- provider 稀疏返回不是请求级异常，保存逐标的失败快照并返回带 warning 的成功响应。
- provider 整体异常不是请求级异常，保存逐标的失败快照，保证失败也可追溯。
- 数据库迁移、写入或读取失败沿用统一内部错误处理，不返回 SQL、数据库路径或 payload 原文。
- 聚合引用必须只包含已成功写入的报价快照 ID。

## 测试设计

### CSV

- Windows API 测试验证持仓 CSV 导入成功。
- Windows API 测试验证自选 CSV 导入成功。
- 错误表头仍返回 422 `validation_error`。
- service 抛出异常时临时文件仍被清理。

### 模型与存储

- `MarketInputSnapshot` 拒绝无时区 datetime。
- 报价 repository 保存并读取 OK、PARTIAL、STALE、FAILED 四种状态。
- 聚合 repository 保存后可按 ID 和 latest 完整往返。
- migration 可重复执行并创建表与索引。

### 采集工作流

- 只采集持仓和启用的自选股，不采集关闭的自选股。
- 同时来自持仓和自选的股票只采集一次。
- provider 稀疏返回时补充失败快照。
- provider 返回额外股票时忽略并记录 warning。
- provider 抛出异常时保存逐标的失败快照，异常内容经过清理。
- 空决策集合不调用 provider并保存空快照。
- 聚合 `data_time` 取有效报价中的最早时间。

### 入口与回归

- CLI 输出快照摘要。
- API 创建、读取 latest 和按 ID 读取快照。
- API 需要认证且不存在时返回稳定错误契约。
- 完整 Python 测试通过。
- 前端单元测试和构建保持通过；本期不修改前端页面。

## 文档同步

实现时同步更新：

- `docs/data-sources.md`：说明首期报价快照范围、时间语义和失败记录。
- `docs/api.md`：记录市场快照接口。
- `README.md`：记录 CLI 手动采集命令。

## 验收标准

- Windows 下四个现有 CSV API 测试通过，不再发生临时文件占用错误。
- 每次采集都保存股票池快照、每个请求标的的报价快照和一个聚合快照。
- 每个聚合报价引用都能读取到对应内部 `QuoteSnapshot`。
- 关闭计划开关的非持仓自选股不会进入采集请求。
- 失败和稀疏响应不会静默丢失，且不会导致整次采集不可追溯。
- 市场时间和系统获取时间可明确区分。
- 快照中不出现第三方原始字段或敏感凭据。
- CLI 和 API 共享同一采集 service。
- 本期不改变策略、风控、计划、建议、通知或调度行为。
