# 后台 HTTP API

## 安全边界

HTTP API 是后台服务接口，只维护本地台账、观察池、快照、计划、建议反馈和调度状态。它不自动真实下单，不模拟点击或控制真实交易客户端，不读取、保存或提交真实券商账号、密码、cookie、token 或 API key。

API 写入的持仓和资金数据等价于用户手动维护本地台账。所有真实交易仍必须由用户在交易软件中人工确认，API 输出和快照不得被描述为保证收益或确定性交易结果。

## 认证

服务未完成访问密码设置时允许启动。`GET /api/v1/service/status` 未携带令牌时只返回 `{"auth_status": "setup_required"}` 或 `{"auth_status": "configured"}`，不返回完整调度、路径或运行细节。完整服务状态需要 `Authorization: Bearer <access_token>`。

未设置密码时，公开 bootstrap 接口只用于读取认证启动状态和完成本地访问密码初始化：

```text
GET /api/v1/service/status
POST /api/v1/auth/setup-password
POST /api/v1/auth/login
```

`POST /api/v1/auth/setup-password` 只在未配置访问密码时可用，设置成功后服务进入 `configured` 状态；再次调用会失败并返回 `auth_already_configured`。`POST /api/v1/auth/login` 在密码未设置前返回 `auth_setup_required`，设置后使用本地访问密码换取 bearer token。

业务接口和 `GET /api/v1/auth/me` 必须携带 `Authorization: Bearer <access_token>`：

```text
Authorization: Bearer <access_token>
```

缺失、格式错误或无效 token 均返回 `unauthorized`。`POST /api/v1/auth/logout` 不做服务端 token 撤销，只返回成功确认；客户端应丢弃本地 token，已签发 token 依靠过期时间失效。

## 错误格式

API 错误统一返回 JSON 对象，顶层字段为 `"error"`：

```json
{
  "error": {
    "code": "auth_setup_required",
    "message": "api password setup required",
    "details": {}
  }
}
```

`code` 是稳定错误码，`message` 是面向调用方的简短说明，`details` 用于保存字段级或上下文信息。

## 持仓接口

```text
GET /api/v1/positions
GET /api/v1/positions/{symbol}
POST /api/v1/positions
PUT /api/v1/positions/{symbol}
DELETE /api/v1/positions/{symbol}
POST /api/v1/positions/import
POST /api/v1/positions/import-csv
GET /api/v1/positions/export-csv
```

这些接口维护手动持仓台账，不从真实券商账户、东方财富模拟组合或行情数据推断真实持仓、成本、数量或可用数量。

## 资金接口

```text
GET /api/v1/cash/account
POST /api/v1/cash/account
POST /api/v1/cash/transfers
POST /api/v1/cash/adjustments
GET /api/v1/cash/transactions?limit=20
```

这些接口维护手动资金账户。模拟银证转入、模拟银证转出和现金校准只改变本地资金口径，不代表真实银行或券商账户已经发生交易。

## 账户估值接口

```text
GET /api/v1/account/snapshot
GET /api/v1/account/snapshot?fresh=true
POST /api/v1/account/snapshots
GET /api/v1/account/snapshots/latest
```

账户快照可读取手动持仓台账、手动资金账户和行情数据，并写入本地账户快照。`GET /api/v1/account/snapshot?fresh=true` 生成并保存新的账户快照；不带 `fresh=true` 时返回已保存的最新快照。行情缺失或覆盖不足时，快照必须显式保留状态，不得把不完整估值伪装成完整账户估值。

## 自选置顶观察池接口

```text
GET /api/v1/watchlist/pinned
POST /api/v1/watchlist/pinned
PUT /api/v1/watchlist/pinned/{symbol}
DELETE /api/v1/watchlist/pinned/{symbol}
POST /api/v1/watchlist/pinned/import
POST /api/v1/watchlist/pinned/import-csv
GET /api/v1/watchlist/pinned/export-csv
POST /api/v1/watchlist/pinned/sync
```

自选置顶接口只维护本地观察池镜像，不读取真实券商账户，也不代表真实持仓或交易。字段为 `symbol`、`name`、`rank`、`plan_enabled`、`note`，响应额外包含 `source` 和 `updated_at`。

`source` 取值为 `manual`、`synced`、`manual_synced`。手动新增、更新和导入使用 `manual`；同步接口会把第三方新增股票记为 `synced` 且默认 `plan_enabled=false`，已经被人工维护过的股票保留本地 `plan_enabled` 和 `note`，并记为 `manual_synced`。

`plan_enabled` 只控制非持仓自选股票是否进入计划和建议扫描。手动持仓台账中的股票始终作为持仓来源进入计划。

## 股票池接口

```text
GET /api/v1/universe
POST /api/v1/universe/snapshots
GET /api/v1/universe/snapshots/latest
```

股票池由手动持仓台账和自选置顶观察池构建。持仓来源优先，`plan_enabled_source=holding`；非持仓自选来源保留自选排序和 `plan_enabled_source=watch_pinned`。

## 市场快照接口

```text
POST /api/v1/market/snapshots
GET /api/v1/market/snapshots/latest
GET /api/v1/market/snapshots/{snapshot_id}
```

三个接口都是业务接口，必须携带 `Authorization: Bearer <access_token>`；缺失、格式错误或无效 token 返回 `unauthorized`。`POST` 通过共享 `MarketSnapshotService` 和当前市场 provider 配置执行一次采集，成功时返回 `201`，响应只包含 `snapshot_id` 和聚合 `snapshot`。两个 `GET` 只读取已经保存的聚合快照，不重新请求行情。

采集范围仅包含手动持仓和 `plan_enabled=true` 的非持仓自选标的。关闭计划的非持仓自选被排除，同时属于持仓和自选的股票只采集一次。provider 稀疏返回或整体失败时，请求仍成功保存可追溯的逐标的失败记录和告警；provider 返回的额外标的会被忽略并告警。

最新快照或指定 ID 不存在时返回 `404` 和稳定错误码 `market_snapshot_not_found`；按 ID 查询的 `details` 包含 `snapshot_id`。不支持的市场 provider 沿用 `422 validation_error`，存储或读取失败沿用经过清理的 `500 internal_error`，均不得暴露数据库内容、第三方原始响应或凭据。

市场快照只保存本地可追溯输入，不下单、不控制真实交易客户端，也不读取或修改真实券商凭据或账户。本期快照尚未被计划、策略、建议、通知或调度流程消费。

## 数据源密钥接口

```text
GET /api/v1/datasource/eastmoney/status
PUT /api/v1/datasource/eastmoney/key
DELETE /api/v1/datasource/eastmoney/key
POST /api/v1/datasource/eastmoney/check
```

数据源接口只维护本地东方财富相关 API key 状态，响应只返回 `configured`、`missing` 或 `invalid` 等状态和检查时间，不返回密钥明文。密钥不得写入 git、结构化日志、前端 localStorage 或测试快照。东方财富妙想使用的 `MX_APIKEY` 仍只能来自环境变量或未入库本地配置。

## 计划接口

```text
POST /api/v1/plans
GET /api/v1/plans/latest
GET /api/v1/plans/{plan_id}
```

计划接口从手动持仓台账和自选置顶生成本地交易计划，并同步保存当时使用的股票池快照。未接入行情时，计划只生成保守候选动作、台账成本附近的关键价位和明确失效条件，不伪造实时价格。

非持仓自选股票只有 `plan_enabled=true` 时才会进入 `watch_symbols`。持仓股票始终进入 `holding_symbols`，即使同一股票在自选置顶中关闭了计划。

## 建议接口

```text
POST /api/v1/recommendations/scan
GET /api/v1/recommendations
GET /api/v1/recommendations/{recommendation_id}
```

建议扫描读取最新有效计划、手动持仓台账、最近账户快照和计划对应股票池快照，生成本地可追溯建议并保存。当前实现不触发外部行情抓取，不自动下单，不控制真实交易客户端；数据不足时仅输出保守持有或观察建议，并保留风险说明和失效条件。

`POST /api/v1/recommendations/scan` 返回：

```json
{
  "count": 0,
  "recommendations": []
}
```

其中 `recommendations` 中每条记录都必须包含 `reason`、`risk.invalid_if`、`valid_until`、`data_time`、持仓上下文、账户上下文和价格上下文。最新计划不存在时返回 `plan_not_found`；计划不是 `active` 或已超过 `valid_until` 时返回 `plan_not_scannable`。

## 反馈接口

```text
POST /api/v1/feedback
GET /api/v1/feedback?recommendation_id=rec-001&limit=50
```

反馈接口记录人工是否执行建议、实际成交价、成交数量和备注。写入反馈后，后端会在同一事务中把关联通知标记为 `feedback_recorded`。反馈接口不得修改手动持仓台账、手动资金账户、现金余额、净本金或账户快照；真实成交后的权威持仓和资金变化仍必须由用户通过台账和资金入口手动维护。

## 通知与审计状态

通知摘要状态为 `unread`、`read`、`feedback_recorded`。`feedback_recorded` 只表示已记录人工反馈，不表示系统确认真实成交或真实账户已经变化。

当前后端已具备通知与审计日志持久化服务，反馈记录会更新已存在的关联通知状态；通知创建、审计写入工作流和通知/审计 HTTP 读取路由尚未作为稳定 API 挂载。前端或外部调用方应把 `/notifications`、`/audit` 视为后续接口，不应依赖其在当前后端可用。

## 服务与调度接口

```text
GET /api/v1/service/status
POST /api/v1/service/scheduler/start
POST /api/v1/service/scheduler/stop
POST /api/v1/service/run-once
```

调度 start/stop 只切换本地调度状态和本地后台任务，不触发真实下单，不控制真实交易客户端，也不修改真实券商账户。`run-once` 只执行一次本地账户快照作业。

默认调度口径：

- 账户快照轮询：每 180 秒执行一次，可通过 `QT_INTRADAY_INTERVAL_SECONDS` 调整。
- 收盘计划：周一至周五 15:30（`Asia/Shanghai`）生成下一工作日计划。
- 盘中触发：周一至周五 09:35-11:30、13:00-13:59、14:00-14:55 扫描最新有效计划并生成建议。
- 已启用调度的服务重启后，默认先执行一次账户快照；配置项为 `QT_SERVICE_RUN_ON_START_WHEN_SCHEDULER_ENABLED`。

当前调度只按周一至周五和配置时区工作，不包含 A 股节假日交易日历。

## CLI 关系

`qt service run` 启动统一后台服务，组合 HTTP API 和调度器。`qt service debug-run --once` 仍是本地调试快照辅助入口，用于手动执行一次快照检查。

`qt watchlist add/update/remove/list/import/export/sync` 使用与自选置顶 API 相同的本地观察池逻辑。`qt market snapshot` 与市场快照 `POST` 接口共享 `MarketSnapshotService` 和市场 provider 配置，只输出内部快照摘要，不输出第三方原始响应。`qt plan generate --date YYYY-MM-DD` 和 `qt plan latest` 使用与计划 API 相同的生成和读取逻辑。`qt recommendations scan`、`qt recommendations list` 和 `qt recommendations show <recommendation_id>` 使用与建议 API 相同的扫描和读取逻辑。

HTTP API、CLI 和后台调度器共享相同的 service、repository、adapter、风控和审计日志逻辑，不为不同入口维护独立口径。
