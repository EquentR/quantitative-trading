# 后台 HTTP API

## 安全边界

HTTP API 是后台服务接口，只维护本地台账、观察池、证券目录、候选预览、快照、计划、建议反馈和调度状态。它不自动真实下单，不模拟点击或控制真实交易客户端，不读取、保存或提交真实券商账号、密码、cookie 或 token。东方财富/妙想 Key 只按下述本地数据源凭据口径保存，不属于券商凭据。

API 写入的持仓和资金数据等价于用户手动维护本地台账。所有真实交易仍必须由用户在交易软件中人工确认，API 输出和快照不得被描述为保证收益或确定性交易结果。

服务默认监听 `0.0.0.0:8000`，用于用户明确要求的局域网访问。除认证 token 外，本项目不提供 TLS、反向代理、防火墙或公网暴露保护；不得把该监听地址理解为可以直接暴露到公网。需要跨主机访问时应在可信局域网或额外网络防护后使用，也可以通过 `QT_API_HOST=127.0.0.1` 收紧为仅本机监听。

SMTP 密码和东方财富/妙想 Key 经用户确认后允许明文保存在本地 SQLite。读取接口只能返回脱敏配置状态，不得返回原值；日志、审计、通知、错误、测试快照和 outbox 不得包含这些凭据。数据库和备份会包含明文凭据，必须保持 git 忽略并限制本机文件权限。

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

## 统一分页

计划、建议、通知、反馈、审计、邮件投递、行情扫描器和运行记录等列表统一接受 `page` 与 `page_size`，并返回：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 20
}
```

各资源可以有不同默认值和最大 `page_size`，但分页列表不得返回裸数组或暴露存储层游标；排序必须稳定。日 K、资金流等时间序列的 `limit` 是最大数据窗口，不属于列表分页。

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

账户快照只由统一盘中 `DecisionWorkflow` 使用本轮固化行情生成。`GET /api/v1/account/snapshot` 和 `GET /api/v1/account/snapshots/latest` 只返回已保存结果；`GET /api/v1/account/snapshot?fresh=true` 固定返回 HTTP `410 account_fresh_snapshot_retired`，`POST /api/v1/account/snapshots` 固定返回 HTTP `410 account_snapshot_create_retired`，两者都指向 `POST /api/v1/service/workflows/intraday/run`，且不得调用行情 provider。行情缺失或覆盖不足时，已保存快照必须显式保留状态，不得把不完整估值伪装成完整账户估值。

## 候选预览与本地观察池接口

```text
GET /api/v1/watchlist/pinned
POST /api/v1/watchlist/pinned
PUT /api/v1/watchlist/pinned/{symbol}
DELETE /api/v1/watchlist/pinned/{symbol}
POST /api/v1/watchlist/pinned/import
POST /api/v1/watchlist/pinned/import-csv
GET /api/v1/watchlist/pinned/export-csv
POST /api/v1/watchlist/pinned/sync
POST /api/v1/watchlist/pinned/select
GET /api/v1/instruments/eastmoney-candidates
GET /api/v1/instruments/search?q=<name-or-code>
```

观察池接口只维护本地记录，不读取真实券商账户，也不代表真实持仓或交易。东方财富 GET 和搜索 GET 只创建默认 10 分钟的标准化预览，不修改观察池或东方财富账户。搜索范围只包含沪深 A 股和沪深场内 ETF，最多 50 项；北交所、港美股、LOF、封闭式基金、REIT 和其他基金不进入可选择范围。搜索不要求东方财富 Key。

两个候选 GET 返回：

```json
{
  "preview_id": "11111111-1111-4111-8111-111111111111",
  "source": "eastmoney_watchlist",
  "query": null,
  "created_at": "2026-07-15T10:00:00+08:00",
  "expires_at": "2026-07-15T10:10:00+08:00",
  "items": [],
  "warnings": []
}
```

候选项包含代码、名称、`SH/SZ`、`a_share/etf/unknown`、`t0/t1/unknown`、涨跌停比例、元数据来源和检查时间、供应商排序、是否已监控、是否可选择及 warnings。客户端确认只允许提交：

```json
{
  "preview_id": "11111111-1111-4111-8111-111111111111",
  "symbols": ["600519", "510300"]
}
```

确认在单一事务内增量写入，任一选中代码无效则整批不写。新验证 A 股和 ETF 默认启用计划；已有记录保留当前 `plan_enabled` 和备注；交易制度未知的 ETF 强制关闭计划；unknown 候选不可选择。响应为 `{items,warnings}`，其中 `items` 是最终观察池记录和实际启用状态。

`source` 取值为 `manual`、`synced`、`manual_synced`。搜索确认和手动维护使用 `manual`；东方财富候选确认使用 `synced`；两种来源先后命中同一记录时使用 `manual_synced`。远端空列表、候选减少或少选不会删除本地记录。JSON/CSV 导入继续全量替换当前观察池，调用方必须明确提示。导入入口会先尽力刷新证券目录，无法验证的记录兼容保留但强制关闭计划。默认响应为 `{items,warnings}`，确保调用方不会静默丢掉目录降级和逐标的禁用原因；仅旧调用可显式使用 `?response=legacy` 获取观察池数组。

旧 `POST /api/v1/watchlist/pinned/sync` 不再接受调用方伪造远端列表。认证请求固定返回 HTTP `410 watchlist_sync_payload_retired`，并在 details 中给出候选预览和选择入口，不写数据库。

`plan_enabled` 控制非持仓观察证券是否进入收盘计划和盘中展示采集；只有当日活动计划内标的才能参与盘中决策。手动持仓台账中的证券始终作为持仓来源进入计划。

## 证券池接口

```text
GET /api/v1/universe
POST /api/v1/universe/snapshots
GET /api/v1/universe/snapshots/latest
```

证券池由手动持仓台账和本地观察池构建。持仓来源优先，`plan_enabled_source=holding`；非持仓观察来源保留排序和 `plan_enabled_source=watch_pinned`。成员引用后端验证的交易所、证券类型、T+0/T+1 制度、涨跌停比例及元数据来源；预览候选不进入证券池。

## 行情、工作流运行和数据引用

```text
POST /api/v1/market/snapshots
GET /api/v1/market/snapshots/latest
GET /api/v1/market/snapshots/{snapshot_id}
GET /api/v1/market/symbols?page=1&page_size=50
GET /api/v1/market/symbols/{symbol}/overview
GET /api/v1/market/symbols/{symbol}/daily-bars?limit=250
GET /api/v1/market/symbols/{symbol}/money-flow?limit=60
GET /api/v1/market/symbols/{symbol}/minute-bars?trade_date=YYYY-MM-DD
GET /api/v1/market/symbols/{symbol}/intraday-strength/latest
GET /api/v1/market/runs?page=1&page_size=50
GET /api/v1/market/runs/{run_id}
GET /api/v1/market/snapshots/{snapshot_id}/trace?symbol=600000
```

这些接口都是认证业务接口。快照 `POST` 和 `DecisionWorkflow` 共享 adapter、repository 和内部模型；读取接口只查询已经保存的后端事实，不因 Web 刷新重新请求 AkShare。行情扫描器的标的集合实时读取手动持仓和当前 `plan_enabled=true` 的观察池，确保人工选择或开关变更立即反映到行情工作台；盘中工作流为这些标的保存报价、分钟线和强弱，但只有持仓与活动计划标的进入决策链。报价、计划、建议和数据引用仍读取各自已固化的快照，尚未采集行情的新标的明确显示 unavailable。扫描器不执行全市场搜索；准备页的证券目录搜索是独立的人工提交操作，不进入交易工作流调度。

日 K 最多返回 250 个交易日的前复权 OHLC、成交量、成交额和后端计算的 MA5/10/20/60。A 股资金流最多返回 60 个交易日的主力、超大单、大单、中单和小单净额及净占比；ETF 返回稳定 `not_applicable`，不表示 provider 失败，也不能作为资金确认。日 K 或适用的资金流尚未首次回填时返回 unavailable 和空集合，前端显示中性待回填状态；真实 provider 失败仍显示错误。分钟接口只返回指定交易日且窗口受限的 1 分钟事实、后端 VWAP 和建议发生点；强弱接口返回组件、方向、理由、覆盖率、规则版本和降级原因。前端不得补算这些字段或证券交易制度。

`/market/runs` 和详情返回 capture run、逐标的逐数据集质量及运行成本。run 字段包含工作流类型、交易日、周期起止、幂等键、开始/结束和计算后的 `duration_ms`、请求/处理标的数、`provider_calls/provider_duration_ms`、返回/写入/清理行数、计划/建议/通知/邮件 outbox 数量、重试和最终错误摘要。`dataset_counts` 按 `quote/daily_bar/money_flow/minute_bar/intraday_strength` 汇总 `complete/degraded/failed/stale` 数量。trace 接口必须同时提供 `snapshot_id` 和六位 `symbol`，因为一个市场输入快照包含多个标的；响应解析 quote、history、money flow、intraday strength、计划和建议引用，并返回实际生效阈值。缺少 symbol 返回校验错误，不能任意选择一个标的。

盘中 stale 默认阈值由 `QT_MARKET_STALE_TRADING_MINUTES=6` 配置，按有效交易分钟计算；午休和非交易时段不累计。API 返回原始 `data_time`、质量和快照中的实际阈值，不用请求时间掩盖旧数据。

provider 稀疏返回或逐标的失败必须保存为可追溯质量结果，不能用空值冒充成功；数据库或契约失败使用安全的统一错误格式。任何接口都不得返回第三方原始 payload、数据库路径或凭据。

## 数据源密钥接口

```text
GET /api/v1/datasource/eastmoney/status
PUT /api/v1/datasource/eastmoney/key
DELETE /api/v1/datasource/eastmoney/key
POST /api/v1/datasource/eastmoney/check
```

PUT 经用户确认将东方财富/妙想 Key 明文保存到本地 SQLite；GET、DELETE 和 POST 响应只返回 `configured`、`missing` 或 `invalid` 等状态、检查时间和脱敏错误，不返回 Key。Key 不得写入 git、结构化日志、审计、前端 Pinia/localStorage 或测试快照；数据库和备份会包含原值。

`POST /api/v1/datasource/eastmoney/check` 真实执行一次只读自选查询，不自动重试。成功或成功空列表为 `configured`；HTTP 401 或供应商业务码 114/115/116 为 `invalid`；业务码 113、超时、网络、非 JSON 或契约变化保留当前配置状态并写安全错误。候选与检查使用以下稳定错误：

- HTTP 409 `datasource_not_configured`
- HTTP 424 `datasource_invalid`
- HTTP 429 `datasource_quota_exceeded`
- HTTP 503 `datasource_unavailable`
- HTTP 502 `datasource_contract_error`
- HTTP 503 `instrument_directory_unavailable`
- HTTP 404 `instrument_preview_not_found`
- HTTP 410 `instrument_preview_expired`
- HTTP 422 `instrument_selection_invalid`

错误响应不得包含 Key、供应商原始响应或本地数据库路径。

## 计划接口

```text
POST /api/v1/plans  (deprecated)
GET /api/v1/plans?page=1&page_size=20
GET /api/v1/plans/latest
GET /api/v1/plans/{plan_id}
```

`POST /api/v1/plans` 已废弃。认证请求固定返回 HTTP `410` 和错误码 `plan_write_deprecated`，不会写入股票池快照或交易计划。唯一支持的手动计划写入路径是 `POST /api/v1/service/workflows/close/run` 或 `qt workflow close`；两者都调用统一收盘 `DecisionWorkflow`，并执行交易日历、运行窗口、人工补跑原因和幂等约束。后台自动收盘任务调用同一个工作流。

计划由收盘 `DecisionWorkflow` 使用当轮固化的报价、前复权日 K、资金流和账户上下文生成。计划包含版本、`source_run_id`、`market_input_snapshot_id`、适用交易日、数据质量、逐标的机器条件、允许/禁止动作、仓位约束、风险和失效条件。市场结构来自均线、区间高低点和 ATR14；手动成本不能冒充支撑或压力。

同一适用交易日只能有一个活动版本，新版本原子激活并将旧版本标记为 `superseded`。计划默认适用于下一 `XSHG` 交易日并在当日 `15:00` 到期。行情和当日日 K 是发布硬前提；A 股资金流缺失可发布带 warning 的降级计划，ETF 合法的 `not_applicable` 不触发该降级。收盘截止仍不满足硬前提时不发布半成品计划。

## 建议接口

```text
GET /api/v1/recommendations?page=1&page_size=20
GET /api/v1/recommendations/{recommendation_id}
GET /api/v1/recommendations/{recommendation_id}/trace
```

建议读取当前 `DecisionWorkflow` 已固化的输入，读取阶段不访问第三方。`buy/add` 必须通过活动计划、多因子确认和硬性风控；无计划的非持仓标的只能 `watch/avoid`。持仓新风险仍可覆盖计划，但缺少报价、成本、数量或可用数量时必须降级为保守 `hold` 和人工复核。

`POST /api/v1/recommendations/scan` 已退役。认证请求固定返回 HTTP `410` 和 `recommendation_scan_retired`，响应 `details.replacement` 指向 `/api/v1/service/workflows/intraday/run`；它不会采集行情或写建议。建议只由统一盘中 `DecisionWorkflow` 生成，列表 `items` 中每条记录都必须包含 `reason`、风险和失效条件、仓位约束、`run_id`、`market_input_snapshot_id`、`plan_id`、逐数据集引用、质量、`valid_until` 和 `data_time`。trace 接口解析台账、账户、行情、历史、资金流、分时、计划、通知和审计引用。

## 反馈接口

```text
POST /api/v1/feedback
GET /api/v1/feedback?recommendation_id=rec-001&page=1&page_size=50
```

反馈接口记录人工是否执行建议、实际成交价、成交数量和备注。写入反馈后，后端会在同一事务中把关联通知标记为 `feedback_recorded`。反馈接口不得修改手动持仓台账、手动资金账户、现金余额、净本金或账户快照；真实成交后的权威持仓和资金变化仍必须由用户通过台账和资金入口手动维护。

## 通知、反馈和审计

```text
GET /api/v1/notifications?status=unread&symbol=600000&page=1&page_size=50
GET /api/v1/notifications/unread-count
POST /api/v1/notifications/{notification_id}/read
GET /api/v1/audit?page=1&page_size=50
GET /api/v1/audit/{audit_id}
```

通知摘要状态为 `unread`、`read`、`feedback_recorded`。`feedback_recorded` 只表示已记录人工反馈，不表示系统确认真实成交或真实账户已经变化。

通知和审计路由是认证稳定接口。标记已读只改变本地处理状态；反馈可以把关联通知更新为 `feedback_recorded`，但不能修改台账或资金。系统故障通知使用 `action=system_alert`，关键工作流故障和 `dead` 邮件都会写入数据库并投射到 Web/API、控制台和 JSONL；SMTP 未配置、禁用或失败不影响这些本地告警。所有列表使用稳定排序和统一分页封装；错误摘要不得包含第三方 payload、路径或凭据。

## SMTP 设置和邮件 outbox

```text
GET /api/v1/settings/notifications/email
PUT /api/v1/settings/notifications/email
DELETE /api/v1/settings/notifications/email/password
POST /api/v1/settings/notifications/email/test
POST /api/v1/notifications/email/settings/test-connection
GET /api/v1/notifications/email-deliveries?status=dead&page=1&page_size=50
GET /api/v1/notifications/email-deliveries/{delivery_id}
POST /api/v1/notifications/email-deliveries/{delivery_id}/retry
```

设置读取响应字段为 `configured`、`host`、`port`、`username`、`sender`、`recipient`、`security`、`enabled`、`password_configured` 和 `updated_at`，永远没有 `password`。`security` 为 `none/starttls/ssl`。PUT 中省略或留空密码表示保留原值；替换和 DELETE 清除是明确操作。两个测试接口都不接收请求体：`test-connection` 只连接、执行 STARTTLS（按配置）并认证，不发送邮件，成功返回 `{"status":"connected"}`；`test` 继续发送测试邮件，成功返回 `{"status":"sent"}`。测试行为均写入审计，响应和审计不包含密码。

outbox 状态为 `pending/sending/retry/sent/dead`。默认首次失败后按 1、5、15、30、60 分钟退避，连同首次尝试最多 6 次；达到上限转为 `dead`。人工 retry 只接受 `retry/dead`，重置投递状态并写审计。邮件 worker 使用原子 claim 和租约恢复，每 15 秒轮询有限批次；邮件失败不能回滚建议或使决策工作流重跑。

## 服务与调度接口

```text
GET /api/v1/service/status
POST /api/v1/service/scheduler/start
POST /api/v1/service/scheduler/stop
POST /api/v1/service/workflows/{workflow_type}/run
```

调度 start/stop 只切换本地调度和持久化启用状态，不触发真实下单，不控制真实交易客户端，也不修改真实券商账户。工作流手动触发必须认证，`workflow_type` 为 `close/intraday/backfill/cleanup`；强制运行、跳过交易日历或超过收盘截止补跑必须记录原因和审计。相同幂等键已有有效执行租约时返回 HTTP `409 workflow_in_progress`，不会重复调用 provider；数据库按 `workflow_type` 对所有 `running` run 建立全局唯一租约，因此 HTTP、CLI、后台调度和多进程重叠周期都会被拒绝或记录 overrun。失败、降级未发布或租约超时的 run 通过原子 compare-and-set 重领并增加 `retry_count`。未处理异常必须把已领取的 run 落成 `failed`。旧 `POST /service/run-once` 和 `GET /account/snapshot?fresh=true` 经认证后固定返回 HTTP `410`，不再形成重复行情采集路径。

`GET /service/status` 的认证响应包含最近任务类型、`last_reason`、开始/结束时间、状态、安全错误摘要、最近计划/建议引用，以及累计 `overrun_count` 和 `skipped_count`。未认证请求仍只返回认证启动状态，不暴露监控细节。

默认调度口径：

- 盘中工作流：`XSHG` 交易日 `09:30-11:30` 和 `13:00-15:00 Asia/Shanghai`，每 3 分钟一次。
- 收盘就绪：`15:15` 首次运行，未就绪每 5 分钟重试，硬截止 `16:30`。
- 原始分钟清理：交易日 `16:35`。
- 邮件 worker：服务运行期间每 15 秒轮询，不受交易时段限制。

各工作流 `max_instances=1` 并合并错过触发。交易日、下一交易日、午休和有效分钟使用 `XSHG` 日历；临时休市等日历外异常仍需人工处理。

服务恢复已持久化为启用的调度器时，普通启动触发仍受 `QT_SERVICE_RUN_ON_START_WHEN_SCHEDULER_ENABLED` 控制；但交易日 `15:15-16:30` 内且当日计划未发布的收盘就绪恢复不受该开关限制，服务启动后立即检查。窗口外不会自动补跑收盘任务，必须通过认证入口带人工原因执行。

## CLI 关系

`qt service run` 启动统一后台服务，组合 HTTP API 和调度器。`qt service debug-run` 与 `qt account snapshot` 已退役并以非零状态退出；单次执行使用 `qt workflow intraday`。

`qt watchlist add/update/remove/list/import/export` 维护本地观察池。`qt watchlist sync` 无 `--symbols` 时只创建并输出东方财富候选预览摘要，带 `--symbols 600519,510300` 时获取一份新预览并确认指定代码；`qt watchlist search <query>` 只创建搜索预览；`qt watchlist select <preview-id> --symbols ...` 消费现有预览。CLI 与 HTTP 共用候选 service、预览 repository 和增量合并逻辑，任一代码无效时整批失败。

数据源 Key 的脱敏状态与只读连接检查通过认证 HTTP API `GET /api/v1/datasource/eastmoney/status`、`POST /api/v1/datasource/eastmoney/check` 或 Web 设置页提供；CLI 不提供 `qt datasource` 命令，也不通过命令参数写入 Key。`qt watchlist sync` 读取本地已配置 Key 并执行一次只读候选查询。`qt market snapshot` 只输出内部快照摘要。`qt plan latest` 只读取已保存计划；`qt plan generate` 已废弃并以非零状态退出，计划生成必须使用 `qt workflow close`。`qt recommendations scan` 也已退役并以非零状态退出，`list/show` 仅读取已保存建议；生成建议必须使用 `qt workflow intraday`。`qt notifications list/unread/read` 对应通知闭环；`qt email status/test/deliveries/retry` 只输出脱敏配置和安全错误摘要。

统一工作流 CLI 范围还包括日 K/资金流基线回填、手动运行收盘或盘中工作流、按交易日显式补跑、通过 `qt service status` 查看持久化调度状态、查看逐数据集质量以及清理超期分钟线。`qt plan latest`、`qt recommendations list/show` 同样支持 `--json`。人类和 JSON 格式表达同一语义。窗口外盘中运行、强制收盘、跳过交易日历或晚于截止补跑要求 `--reason`，并通过隐藏输入提示校验本地 API 访问密码；密码不得出现在命令参数、shell 历史、输出或审计中。错误输出不得包含第三方原始响应、数据库路径或凭据。

所有成功状态变更请求还写入 `api.write.succeeded` 审计，只保存 method、稳定 operation 名和状态码，不读取请求 body、query、Authorization 或密码。领域级审计继续保存资源 ID 和安全摘要。

HTTP API、CLI 和后台调度器共享相同的 service、repository、adapter、风控和审计日志逻辑，不为不同入口维护独立口径。
