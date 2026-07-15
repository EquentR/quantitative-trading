# 东方财富候选选择、证券搜索与 ETF 决策支持设计

## 背景

当前系统已经具备本地自选置顶观察池、JSON/CSV 导入、观察池合并语义、东方财富/妙想 API Key 配置状态以及 Web 准备页。现有 `/api/v1/watchlist/pinned/sync` 只合并调用方提交的列表，`qt watchlist sync` 仍是占位命令，数据源“检查连接”也没有真实访问供应商。

仓库中的 `vendor/miaoxiang/skills/mx-zixuan` 提供东方财富通行证账户自选股查询接口：

- `POST https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get`
- Header 使用 `apikey`。
- 请求体为空 JSON 对象。
- 返回自选股列表、供应商排序及行情展示字段。

该接口只提供“全部自选股”，没有稳定的“是否置顶”字段。因此本期不再把供应商返回列表解释为可直接进入决策的置顶集合，而是把它作为远端候选来源：用户预览并选择后，选中证券才进入本地观察池。

用户还需要按名称或代码搜索不在东方财富自选中的证券，并允许沪深 A 股和沪深场内 ETF 进入同一套收盘计划、盘中策略和风控。ETF 的行情接口、资金流适用性和交易制度与 A 股不同，因此本期必须引入内部证券元数据，不能只在 Web 放开六位代码输入。

## 规约与安全边界

实现必须继续遵守：

- `docs/project-spec.md`
- `docs/trading-policy.md`
- `docs/data-sources.md`
- `docs/recommendation-contract.md`

本期继续保持以下边界：

- 不自动真实下单，不控制真实交易客户端，不绕过人工确认。
- 不读取东方财富模拟组合推断真实持仓、成本、数量、可用数量或现金。
- 手动持仓台账仍是真实持仓权威源，手动资金账户仍是资金权威源。
- 外部字段只在 adapter 内解释，策略、风控、计划和 Web 不依赖妙想或 AkShare 原始字段。
- 本系统只读取东方财富自选，不调用 `mx-zixuan` 的新增或删除接口，不反向修改用户的东方财富账户。
- 供应商候选预览和证券搜索不自动写入本地观察池。
- 所有真实交易继续由用户人工执行。

东方财富/妙想 Key 按用户确认保留现有 SQLite 本地存储方式：

- Web 继续使用 `/api/v1/datasource/eastmoney/key` 配置。
- adapter 通过现有凭据 repository 在后端运行时读取 Key。
- API、Web、日志、审计、异常摘要和测试快照不返回或记录 Key 明文。
- 数据库与 `data/` 继续保持 git 忽略；示例和测试只使用占位值。
- 本期不迁移到环境变量或单独密钥文件。

## 范围

### 本期范围

- 从东方财富妙想获取远端自选候选并生成只读预览。
- 用户多选候选并确认后，增量加入本地观察池。
- 按股票代码或名称搜索沪深 A 股和沪深场内 ETF。
- 用户从搜索结果多选并确认后，增量加入本地观察池。
- 确认加入的证券默认 `plan_enabled=true`。
- 贯穿观察池、股票池、市场输入、计划、策略、风控和建议的证券类型与交易制度元数据。
- A 股与 ETF 各自使用适用的报价、日 K 和分钟线 adapter。
- ETF 资金流明确为“不适用”，不伪装为失败或完整数据。
- ETF 的 T+0/T+1 规则可验证时进入正常决策，无法验证时只允许观察。
- 真实数据源连接检查、CLI 和 Web 操作入口。

### 非目标

- 不同步东方财富模拟组合。
- 不自动定时同步远端自选。
- 不把远端全部自选自动写入本地观察池。
- 不支持北交所、港股、美股、LOF、封闭式基金、REIT、场外基金、债券或其他品种。
- 不调用供应商接口添加或删除东方财富自选。
- 不新增 ETF 专属黑盒模型或未经验证的策略参数。
- 不用代码前缀、名称关键词或模型猜测 ETF 的 T+0/T+1 制度。

## 方案选择

### 采用方案：项目原生 adapter 封装供应商契约

项目新增原生妙想 adapter，复用 vendor skill 中已经记录的官方 URL、认证头、请求结构和响应路径，但不直接 import 或运行 vendor 脚本。

理由：

- vendor 脚本包含 `sys.exit`、终端输出和固定文件目录，不适合作为常驻服务依赖。
- 原生 adapter 可以统一超时、错误分类、字段校验、脱敏和测试注入。
- 核心服务只依赖内部 Protocol 和模型，供应商升级只影响 adapter。
- API、CLI 和 Web 能共享同一套服务逻辑。

### 未采用：直接 import vendor 脚本

改动较少，但脚本会读取环境变量、写文件并终止进程，无法提供稳定的应用层错误契约，也难以安全用于并发请求。

### 未采用：子进程运行 vendor 脚本

隔离性较好，但会引入临时文件、子进程并发、超时、Key 传递和原始响应清理问题，且不利于单元测试。

## 内部模型

### 证券元数据

新增统一内部模型 `InstrumentMetadata`：

```text
symbol: str                  六位代码
name: str
exchange: SH | SZ
instrument_type: a_share | etf | unknown
settlement_cycle: t0 | t1 | unknown
price_limit_ratio: float | null
metadata_source: str
metadata_checked_at: timezone-aware datetime
warnings: list[str]
```

规则：

- 只允许 ASCII 六位数字代码。
- `a_share` 必须属于沪深 A 股目录，固定 `settlement_cycle=t1`。
- `etf` 必须属于沪深交易所 ETF 目录。
- LOF、封闭式基金、REIT 和其他基金即使代码为六位也不得映射为 ETF。
- `price_limit_ratio` 只在权威或可验证元数据明确时保存；无法确定时为 `null`。
- `metadata_source` 和检查时间必须随快照保存，以便解释历史决策。

### 候选预览

新增 `InstrumentCandidate`：

```text
symbol
name
exchange
instrument_type
settlement_cycle
price_limit_ratio
source: eastmoney_watchlist | instrument_search
source_rank: int | null
already_monitored: bool
selectable: bool
warnings: list[str]
```

新增 `InstrumentPreview`：

```text
preview_id
source
query: str | null
created_at
expires_at
items: list[InstrumentCandidate]
warnings: list[str]
```

预览写入的是短期标准化缓存，不是本地观察池。缓存不包含 Key 或供应商原始响应。

### 观察池与下游契约

`WatchPinnedItem`、`UniverseMember`、`PlanSymbolContext`、`MarketPlanSymbolInput`、策略输入、风控输入和 `Recommendation` 增加或引用以下字段：

- `exchange`
- `instrument_type`
- `settlement_cycle`
- `price_limit_ratio`
- `metadata_source`
- `metadata_checked_at`

推荐契约中的证券元数据由后端生成并随建议保存；前端不得根据代码或名称补算。

## 数据存储与迁移

### instruments

新增证券目录与元数据表，按 `symbol` 保存沪深 A 股/ETF 目录和最新已验证内部元数据。外部原始 payload 不入表。字段至少包括：

- `symbol` 主键。
- `name`。
- `exchange`。
- `instrument_type`。
- `settlement_cycle`。
- `price_limit_ratio`。
- `metadata_source`。
- `metadata_checked_at`。
- `is_active`，表示最近一次成功目录刷新仍包含该证券。
- `warnings_json`。

新增 `instrument_catalog_state` 目录刷新状态表，记录每个目录来源的最近成功时间、数据交易日、状态和安全错误摘要。当前 `Asia/Shanghai` 自然日内每个目录最多自动刷新一次。刷新失败但存在历史目录时继续使用历史目录并标记 stale warning；从未成功刷新时，证券搜索返回 `instrument_directory_unavailable`，远端自选仍可预览供应商基础代码和名称，但所有未验证项目 `selectable=false`。

### instrument_previews

新增短期预览缓存表：

- `preview_id` 主键，使用不可预测 UUID。
- `source`。
- `query`，远端自选预览时为空。
- `items_json`，只保存标准化候选。
- `created_at`。
- `expires_at`。

默认有效期为 10 分钟。读取过期预览时返回稳定错误并可惰性删除；后台不需要新增定时任务。

### watch_pinned

观察池继续按 `symbol` 唯一。证券元数据以 `instruments` 表为内部权威来源，观察池响应组合返回元数据。现有 `source=manual|synced|manual_synced` 保持兼容：

- 从东方财富候选确认加入时使用 `synced`。
- 从搜索结果确认加入时使用 `manual`。
- 已存在人工记录又被东方财富候选命中时使用 `manual_synced`。
- 增量确认不得清除已有 `note`。

人工选择使用新的增量 upsert 服务，不调用现有具有“外部集合全量对齐并删除消失项”语义的 `merge_synced_pinned`。因此后续远端空列表、候选减少或用户少选都不会删除本地项目。

现有 JSON/CSV 导入仍保持全量替换语义。导入后必须解析证券元数据；无法可靠解析的记录允许保存，但强制 `plan_enabled=false` 并返回 warning。Web 必须明确提示全量替换语义。

### 旧数据迁移

迁移不能只按代码段猜测证券类型。

- 已有观察股在本地 `instruments` 找到已验证记录时直接关联。
- 没有元数据的旧记录标记为 `instrument_type=unknown`、`settlement_cycle=unknown`。
- 未知记录保留展示和人工备注，但强制退出决策启用集合，等待用户重新搜索或刷新元数据。
- 旧持仓台账数据不删除、不改数量和成本。若持仓证券元数据未知，持仓风险输出降级为保守 `hold` 并要求人工复核。

## Adapter 设计

### MiaoxiangWatchlistAdapter

职责：

- 从现有凭据 repository 获取 Key。
- 调用 `mx-zixuan` 查询接口。
- 验证 HTTP 状态、顶层业务状态和核心响应路径。
- 提取代码、名称和供应商返回顺序。
- 去除重复代码；重复项保留第一次出现的位置并记录 warning。
- 只返回内部远端候选基础模型，不返回行情展示字段或原始 JSON。

超时使用显式可配置值，首版默认 30 秒。请求不自动重试，避免一个人工操作重复消耗供应商额度；用户可以显式刷新。

### InstrumentDirectoryAdapter

职责：建立可搜索、可验证的沪深 A 股和 ETF 目录。

- A 股目录使用 AkShare 的沪深 A 股列表能力并过滤交易所。
- ETF 目录使用 AkShare 的 ETF 列表/行情能力，并以沪深交易所 ETF 产品目录做类型校验。
- 对相同代码冲突、缺失名称、市场不一致或无法确认产品类型的项目标记 `selectable=false` 并返回 warning，不允许进入确认写入。
- 提供按精确代码、代码前缀、名称包含搜索；精确代码优先，其次代码前缀，再按名称相关度和代码稳定排序。
- 搜索只在用户提交查询时执行，不在每次键盘输入时触发第三方请求。
- 查询文本去除首尾空白，长度受限；空查询不执行远程调用。
- 目录刷新成功后搜索只读取本地标准化目录；同一自然日的重复搜索不重复下载全量目录。

本期名称/代码搜索只使用该结构化目录，不调用妙想 `mx-data` 或 `mx-xuangu` 的自然语言接口。这样搜索不消耗妙想额度，也不要求配置东方财富 Key；`mx-zixuan` 只负责远端自选候选来源。

### InstrumentTradingRuleResolver

职责：把已验证的证券类型和交易所产品类别转换为内部交易规则。

- A 股固定映射为 `t1`。
- ETF 只根据上交所 ETF 产品目录的 `ETF_TYPE` 和深交所 ETF 产品目录的“基金类别/投资类别”映射。
- 规则映射必须版本化，并保存元数据来源和检查时间。
- adapter 先通过精确字符串白名单把交易所原始类别归一化为 `cross_border`、`bond`、`gold`、`commodity`、`money_market`、`domestic_equity` 或 `domestic_index`；白名单不做名称包含匹配。
- `cross_border`、`bond`、`gold`、`commodity`、`money_market` 映射为 `t0`。
- `domestic_equity` 和 `domestic_index` 映射为 `t1`。
- 类别缺失、冲突或规则表未覆盖时映射为 `unknown`。
- 不允许通过基金名称包含“黄金”“纳指”等关键词判断结算制度。

### 市场数据路由

市场 adapter 按 `instrument_type` 路由：

| 数据集 | A 股 | ETF |
| --- | --- | --- |
| 报价 | 现有 A 股批量报价及兜底 | ETF 报价 adapter 及现有单票兜底 |
| 前复权日 K | A 股前复权日 K | ETF 前复权日 K |
| 1 分钟线 | A 股分钟线 | ETF 分钟线 |
| 完整资金流 | 现有逐股资金流 | `not_applicable` |

同一轮仍由 `DecisionWorkflow` 统一固化输入。按品种分组后，每个 provider 能力每组最多调用一次；策略、风控、账户估值和 Web 不得直接调用第三方。

ETF 的 `money_flow=not_applicable` 是逐数据集稳定状态。数据集状态枚举增加 `not_applicable`，但整体计划/建议质量仍只使用 `complete/degraded/failed/stale`；合法的不适用状态本身不降低整体质量，不计为 provider 失败，也不能作为资金流确认。ETF 仍需日线结构和盘中强弱两个独立有效因子才能通过买入侧门禁。

## 业务流程

### 东方财富候选预览

1. 用户在 Web 或 CLI 显式请求刷新东方财富候选。
2. 应用服务调用 `MiaoxiangWatchlistAdapter` 获取远端列表。
3. 使用 `InstrumentDirectoryAdapter` 和规则解析器验证、分类每个候选。
4. 过滤北交所、港股、美股和非 ETF 基金；被过滤数量进入 warnings。
5. 保存 10 分钟标准化预览缓存并返回 `preview_id`。
6. 本地 `watch_pinned`、股票池、计划和建议均不改变。

远端空列表是成功空结果，不删除或改变本地观察池。

### 名称或代码搜索

1. 用户提交名称或代码查询。
2. 后端按精确代码、代码前缀和名称包含搜索沪深 A 股/ETF 目录。
3. 返回有上限的稳定排序结果和 `preview_id`。
4. 搜索结果不要求存在于东方财富自选，也不要求配置东方财富 Key，不修改东方财富账户或本地观察池。

首版默认最多返回 50 项，防止宽泛名称造成超大响应。

### 人工确认加入

1. 客户端提交 `preview_id` 和选中的 `symbols`，不提交可信的名称、类型或交易规则。
2. 后端读取未过期的标准化预览并校验每个代码都存在且可选择。
3. 批量确认使用单一事务：任一项目无效时全部不写入。
4. 写入或更新 `instruments`，再增量合并 `watch_pinned`。
5. 新项目默认 `plan_enabled=true`；已有项目保留当前 `plan_enabled`，重复确认不会重新开启用户已经关闭的计划。
6. 已有项目保留 `note`；人工维护过的项目不被远端候选覆盖成本地默认值。
7. 已确认 `instrument_type=etf` 但 `settlement_cycle=unknown` 时是安全例外：允许加入展示，但强制 `plan_enabled=false` 并返回 warning。
8. `instrument_type=unknown` 的候选 `selectable=false`，不能通过选择接口加入；旧数据和手动 JSON/CSV 导入中的未知证券只为兼容而保留展示，并强制关闭计划。
9. 成功后使观察池、股票池、计划、建议和服务状态相关查询失效并刷新。

确认操作只消费预览缓存，不再次调用供应商，避免重复消耗额度。预览过期后要求用户重新刷新。

## ETF 计划、策略与风控

### 决策启用

- `a_share/t1`：按现有 A 股规则参与计划和盘中决策。
- `etf/t0` 或 `etf/t1`：在行情输入满足要求时参与计划和盘中决策。
- `instrument_type=unknown` 或 `settlement_cycle=unknown`：不得进入买卖动作计算，只允许 `watch`；持仓场景降级为 `hold` 和人工复核。

### 可用数量与 T+0/T+1

手动台账的 `quantity` 和 `available_quantity` 始终优先于证券制度推算：

- 系统不因 ETF 为 T+0 就自动增加可卖数量。
- `available_quantity=0` 时仍禁止 `sell/reduce`。
- T+0 ETF 不附加 A 股 T+1 文案，但必须服从最新手动可用数量。
- T+1 ETF 继续执行与 A 股一致的当日可卖约束。

### 涨跌停与未知规则

- `price_limit_ratio` 明确时才执行对应的涨跌停判定。
- 比例未知时不猜测是否封板，相关数据质量降级并加入风险提示。
- 未知涨跌停比例不会被伪装为“未涨跌停”；若买入规则依赖该判断，则动作降级。

### 策略因子

- ETF 继续使用可解释的日线结构、量价和盘中强弱规则。
- ETF 资金流为不适用，不提供确认也不作为失败惩罚。
- `buy/add` 仍要求活动计划、至少两个独立有效因子和完整硬性风控。
- 本期不新增 ETF 专属阈值；共用阈值必须在测试中证明模型字段和单位一致，否则先降级仅观察。

## API 设计

所有接口继续要求 bearer token。

### 候选与搜索

```text
GET /api/v1/instruments/eastmoney-candidates
GET /api/v1/instruments/search?q=<name-or-code>
POST /api/v1/watchlist/pinned/select
```

现有 `POST /api/v1/watchlist/pinned/sync` payload 同步入口退役，认证请求固定返回 HTTP `410 watchlist_sync_payload_retired`，并指向候选预览和选择接口。客户端不能再提交任意列表并标记为远端同步来源。

两个 GET 返回：

```json
{
  "preview_id": "uuid",
  "source": "eastmoney_watchlist",
  "query": null,
  "created_at": "2026-07-15T10:00:00+08:00",
  "expires_at": "2026-07-15T10:10:00+08:00",
  "items": [],
  "warnings": []
}
```

确认请求只接受：

```json
{
  "preview_id": "uuid",
  "symbols": ["600519", "510300"]
}
```

响应包含最终观察池项目、实际启用状态和 warnings。

### 数据源检查

`POST /api/v1/datasource/eastmoney/check` 改为调用一次只读自选查询：

- 成功或成功空列表：状态为 `configured`。
- HTTP 401 或供应商业务码 `114/115/116`：状态为 `invalid`。
- 供应商业务码 `113`：保留配置状态并记录脱敏的额度错误，不误判 Key 无效。
- 超时、网络错误、非 JSON 或结构变化：保留配置状态并记录脱敏错误，不误判 Key 无效。

### 稳定错误

- HTTP 409，Key 缺失：`datasource_not_configured`。
- HTTP 424，Key 无效：`datasource_invalid`。
- HTTP 429，调用额度耗尽：`datasource_quota_exceeded`。
- HTTP 503，网络或超时：`datasource_unavailable`。
- HTTP 502，供应商响应不满足契约：`datasource_contract_error`。
- HTTP 503，证券目录首次刷新失败且无缓存：`instrument_directory_unavailable`。
- HTTP 404，预览不存在：`instrument_preview_not_found`。
- HTTP 410，预览过期：`instrument_preview_expired`。
- HTTP 422，选中代码不属于预览或不可选择：`instrument_selection_invalid`。

错误响应不包含 Key、供应商原始响应或本地数据库路径。

## CLI 设计

现有 `qt watchlist sync` 改为真实远端候选预览：

- 无 `--symbols` 时只输出 `preview_id`、候选数量、可选择数量、已监控数量和 warnings，不修改观察池。
- 显式传入 `--symbols 600519,510300` 时，命令获取一份新预览并只确认指定代码。
- 任一代码不在预览中时整批失败且不写库。

新增：

```text
qt watchlist search <query>
qt watchlist select <preview-id> --symbols 600519,510300
```

`search` 只生成预览；`select` 消费现有预览。CLI 和 HTTP 共用同一个候选服务、预览 repository 和观察池合并服务。

## Web 设计

“准备”页的自选置顶观察池保留现有新增、JSON/CSV 导入和导出能力，并增加：

- “从东方财富选择”命令，打开候选选择区域。
- 股票名称或代码搜索框，只有提交搜索时发请求。
- 候选表格显示代码、名称、A 股/ETF、SH/SZ、T+0/T+1/未知、是否已监控和警告。
- 多选复选框与明确的“加入监控”命令。
- 确认前说明加入后默认启用计划。
- 未知 ETF 显示“仅观察，交易制度待确认”，不可被默认启用。
- 成功后刷新观察池和下游查询；不把候选列表当成本地观察池状态。

页面必须分别处理 loading、空结果、Key 缺失、Key 无效、额度耗尽、网络错误、预览过期和部分候选被过滤。现有 JSON/CSV 导入旁增加“全量替换当前观察池”的明确提示。

## 错误处理与一致性

- 远端候选或搜索失败时不修改本地观察池。
- 候选预览创建和确认是两个显式步骤。
- 确认批量写入是原子操作。
- 预览只保存内部标准化结果，不保存第三方原始 JSON。
- 供应商返回额外市场或品种时过滤并告警，不能扩大允许范围。
- 候选分类失败不影响其他候选预览，但失败项目不可默认启用。
- 数据库写入、模型不变量或预览引用错误终止整次确认。
- 远端空列表不触发本地删除。
- 同一预览重复确认同一批代码保持幂等，不重复创建观察池记录。
- 本期不将预览失败或供应商故障接入交易工作流调度；用户操作失败通过 API/Web/CLI 返回。

## 文档同步

实现时同步更新：

- `docs/project-spec.md`：候选预览、人工确认和 ETF 范围。
- `docs/trading-policy.md`：A 股/ETF 证券制度、T+0/T+1 和未知规则降级。
- `docs/data-sources.md`：妙想自选 adapter、证券目录、ETF 数据路由、Key 现有 SQLite 口径。
- `docs/recommendation-contract.md`：证券元数据和 ETF 数据适用性。
- `docs/api.md`：候选、搜索、确认和稳定错误。
- `README.md`：Web 与 CLI 操作步骤及 ETF 安全限制。

## 测试设计

### Adapter 契约测试

- 妙想自选成功、成功空列表、重复代码和供应商顺序。
- HTTP 401、业务码 113/114/115/116。
- 超时、网络错误、非 JSON 和核心路径缺失。
- 响应和错误不泄露 Key 或原始 payload。
- A 股/ETF 目录过滤 SH/SZ 之外市场和非目标基金。
- 证券目录每日最多自动刷新一次，失败时使用 stale 缓存，无缓存时返回稳定错误。
- 搜索精确代码、代码前缀、名称包含、稳定排序和 50 项上限。
- ETF 类型与 T+0/T+1 规则映射；缺失和冲突映射为 unknown。

### Repository 与 service 测试

- 预览创建不修改 `watch_pinned`。
- 预览缓存不包含 Key 或原始响应。
- 过期和不存在预览拒绝确认。
- 选中代码不属于预览时原子失败。
- 批量确认成功、重复确认幂等、保留已有备注和来源合并。
- 新增 A 股和已验证 ETF 默认 `plan_enabled=true`。
- 已确认 ETF 但结算制度未知时允许加入并强制 `plan_enabled=false`；证券类型未知时选择接口拒绝加入。
- 远端空列表不删除本地观察池。
- 旧数据迁移不猜测证券类型、不修改持仓数量和成本。

### 市场、计划、策略与风控测试

- A 股与 ETF 路由到正确报价、日 K 和分钟 adapter。
- 同一轮按品种分组且不重复采集。
- ETF 资金流保存为 `not_applicable`。
- A 股 T+1 行为保持回归兼容。
- T+0 ETF、T+1 ETF 和未知 ETF 的动作门禁。
- 手动 `available_quantity=0` 始终禁止 `sell/reduce`。
- 未知涨跌停比例不伪装为可交易。
- ETF 在日线结构和盘中强弱两个有效因子满足时可通过因子数量门禁。
- 未知元数据持仓降级为 `hold` 并带人工复核原因。
- 建议与 trace 返回实际证券元数据和规则来源。

### API、CLI 与 Web 测试

- 数据源检查真实调用只读接口并正确更新状态。
- 候选、搜索、确认接口认证和稳定错误码。
- 旧 payload 同步 API 固定返回 `410 watchlist_sync_payload_retired` 且不写库。
- CLI 无 `--symbols` 只预览，显式选择才写库。
- Web 候选多选、搜索、防重复、确认、预览过期和下游刷新。
- Web 不保存或回填 Key。
- JSON/CSV 导入显示全量替换提示。

### 完整验证

- `.venv/bin/python -m pytest -q`
- `pnpm -C src/web test`
- `pnpm -C src/web build`
- 与外部供应商隔离的 fake adapter 端到端测试。
- 在用户本地配置有效 Key 后执行一次只读连接检查、候选预览和人工选择 smoke test；不得在测试输出中记录 Key。

## 验收标准

- 配置有效 Key 后，连接检查真实验证妙想只读自选接口。
- Web 和 CLI 可以获取远端自选候选，但预览不会改变本地观察池。
- 用户可以从远端候选中多选并确认加入，且默认启用计划。
- 用户可以按名称或代码搜索沪深 A 股和沪深 ETF，并加入非东方财富自选证券。
- 北交所、港美股、LOF、封闭式基金、REIT 和场外基金不会进入可选择结果。
- 已验证 T+0/T+1 ETF 能使用正确数据路由进入统一决策工作流。
- 无法可靠确认交易制度的 ETF 只能观察，不能产生 `buy/add/sell/reduce`。
- 手动持仓数量、成本和可用数量始终保持权威地位。
- ETF 资金流明确显示不适用，不伪装为成功数据或接口故障。
- 候选确认保留本地备注，批量写入原子且幂等。
- Key 不出现在 API 响应、Web 持久化、日志、审计、测试快照或 git。
- 后端测试、前端测试和生产构建全部通过。
