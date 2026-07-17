# 行情刷新、质量判定与重复建议修复设计

## 背景与现场证据

2026-07-17 的运行数据暴露出四类相关问题：

- 当日新加入的标的可以获得报价和分钟线，但人工刷新不会补齐日 K；盘中快照又只从活动收盘计划继承 history 引用。没有活动计划时，即使本地已有日 K，`history_snapshot_refs` 仍为空。
- 可用报价被总体质量误伤。以 `512480` 为例，最新价和分时强弱可用，但 history 缺失令 overall 变成 failed，决策层随后统一输出“当前行情不可用，暂停价格触发型动作”。
- 午休点击手动盘中工作流时，API 在启动前返回 `422 workflow_outside_session`；交易时段与调度器撞车时返回 `409 workflow_in_progress` 和实际 `run_id`。前端把所有异常统一显示为失败，因此出现“立即失败但后台仍在跑”的假象。
- 每三分钟建议身份按周期保存符合审计契约，但通知去重键错误包含 `run_id`/周期。同一条件的 HOLD 因此产生大量建议列表项和未读通知。

分钟数据还有一条独立的偶发故障路径：adapter 已有东方财富到新浪的双源 fallback，但两个来源同时抛错时，盘中工作流直接进入失败分支，没有读取同一交易日早先保存的分钟缓存。provider 返回空列表时反而会读取缓存，异常与空响应的行为不一致。

## 目标

- 行情页提供一次明确的人工“获取行情”操作，补齐启用标的的日 K，并刷新报价、分钟线和分时强弱。
- 非交易时段允许刷新最近可用展示数据，但不生成账户估值、计划、建议、通知或邮件。
- 新增标的无需等待下一次收盘计划即可获得 K 线；没有活动计划也能在盘中快照中引用可追溯的本地 history。
- quote、history、plan 和 intraday quality 按用途判定，避免将历史上下文缺失误报成当前报价不可用。
- 双分钟源瞬时失败时可以复用同一交易日缓存，并诚实保存 provider 故障、降级和陈旧状态。
- 保留每个三分钟周期的建议审计历史，同时默认只展示每个标的最新状态；相同条件跨周期只生成一条通知。
- 手动刷新遇到已有工作流时跟随实际运行状态，不重复调用 provider，也不立即显示失败。

## 非目标

- 不自动下单，不控制真实交易客户端，不读取或提交券商凭据。
- 不放宽活动计划、多因子确认、证券制度校验或硬性风控门禁。
- 不把非交易时段刷新伪装成盘中决策或强制补跑。
- 不在每三分钟调度中无条件重复调用日 K 和资金流重数据接口。
- 不删除建议、通知或审计历史。
- 不跨交易日复用分钟缓存，不用 `fetched_at` 伪造市场数据时间。

## 方案比较

### 方案一：组合现有工作流并增加显式展示模式

行情页依次运行 `backfill` 和 `intraday` 两个既有工作流。`intraday` 在非交易时段进入显式 `display_only` 模式。两个阶段各自保留幂等键、租约、`run_id`、状态和 warning，前端负责展示阶段进度和部分成功。

该方案复用 adapter、repository、快照、质量和审计边界，不增加第二套行情核心。它需要明确两个阶段不是一个原子事务，并完善 display-only 隔离、日 K 截止日和前端状态机。

### 方案二：每轮盘中调度补日 K

新标的无需人工操作，但重数据源会每三分钟进入盘中故障面，增加延迟、限流和租约占用。即使通过幂等跳过写入，也容易重复做 provider 校正请求。

### 方案三：加入持仓或观察池时同步日 K

首次数据出现更早，但“保存标的”会依赖外部网络，难以清晰表达标的保存成功而行情初始化失败，也不能解决非交易时段、409 跟随、质量误判和通知洪水。

采用方案一。

## 总体数据流

```text
行情页：获取行情
  -> Stage 1: backfill(日 K；A 股适用资金流)
       -> 独立 run_id/status/warnings/reused
  -> Stage 2: intraday
       -> 交易时段: mode=decision
       -> 非交易时段: mode=display_only
       -> 独立 run_id/status/warnings/reused
  -> 等待每个 stage 的真实终态
  -> 刷新行情、建议、通知和运行记录查询
```

两个 stage 串行发起，但不是跨工作流原子事务。Stage 1 成功而 Stage 2 失败时，K 线结果仍保留并在界面显示“日 K 已更新，报价/分时刷新失败”。现有不同 workflow type 的租约仍相互独立；若调度器在两阶段之间或期间启动 intraday，人工流程通过该 intraday 的 `409` 和 `run_id` 跟随它，不假设获得跨类型全局租约。

人工刷新只接受当前统一股票池中已验证且启用的持仓/观察标的。它不能成为任意代码抓取接口。前端默认刷新当前启用股票池；后端继续校验显式 symbols 是该股票池的子集。

## 交易日与日 K 截止日

人工请求保存 `requested_at`、`effective_trade_date` 和 `history_cutoff_date`。三者不能互相替代。

`effective_trade_date` 用于报价、分钟线和强弱：

- 连续竞价、午休和当日收盘后：当前 XSHG 交易日。
- 交易日开盘前：上一 XSHG 交易日。
- 周末或节假日：最近一个已完成的 XSHG 交易日。

`history_cutoff_date` 只允许指向已经完整收盘且日 K 已就绪的交易日：

- 盘中和午休固定为上一 XSHG 交易日。
- 收盘后先按现有 close readiness 规则验证当日日 K；验证成功才使用当日，否则继续使用上一交易日并保存 warning。
- 开盘前、周末和节假日使用最近一个已完成且数据就绪的交易日。

不得在盘中把当天传给 250 日 backfill。否则当天未形成的日 K 会制造假 degraded，并可能被同日幂等复用，阻止收盘后真正回填。

合法新上市标的不足 250 根时，只有满足以下任一证据才能把短历史认定为“上市以来完整”：后端元数据含权威 `listing_date`，且从 listing date 后首个 XSHG 会话至 cutoff 无缺口；或 provider 成功覆盖完整请求窗口，明确没有更早数据，且从首个返回交易日至 cutoff 无缺口。纯本地短缓存无法证明起点时必须请求 provider 补证；provider 也无法证明时保持 unverifiable，不得把缺失前缀猜成新上市。

上市以来完整且达到策略最小历史窗口时为 degraded/usable；不足最小窗口时 history 不可用于结构判断。响应必须保存完整性证据、实际起止、行数和 warning，不能用 250 根硬性期望将新股伪装成 provider failure。

adapter 边界增加独立的 `DailyBarCoverageEvidence` 能力，保存 requested start/end、provider 实际观测窗口、earliest available date、是否完整观测请求窗口和 source。只有 adapter 对其接口契约确认完整窗口响应时才能设置 complete；核心 service 不根据“返回行数少”猜测上市日期。不实现 coverage evidence 的旧 provider/fake 仍可返回 bars，但不足 250 根时保持 unverifiable，直到获得权威 listing date 或覆盖证据。

## Backfill 与不可变 History 固化

backfill 采用 local-first：

1. 从版本化 `daily_bars` 查找 symbol、前复权口径和 `history_cutoff_date` 范围。
2. 校验成员的 symbol、交易日、版本/source/fetched_at、排序、去重、provider 覆盖证据和 content hash。不能要求每个 XSHG 会话都有 bar，因为合法停牌日也没有日 K；完整性由 listing date、已知停牌/复牌事实或 provider 对完整请求窗口的观测证据证明。
3. intraday 只需要固化引用且本地已有经过 backfill 验证的完整覆盖时，直接固化不可变 `HistorySnapshot` 及 members，不调用 provider。
4. 人工 `as_of_mode=latest_complete` 第一次命中新的 cutoff/symbol scope 时继续执行现有最近 5 日 correction，以接收历史修订；同一幂等 scope 的后续请求复用已完成 backfill。显式 CLI backfill 和 close workflow 的 correction 语义保持不变。
5. 存在真实缺口或短历史起点无法证明时，调用对应 A 股或 ETF 日 K adapter，并通过现有 repository 保存版本化事实后重新固化快照。
6. 每个 snapshot 保留成员版本、source、fetched_at、范围、行数、短历史完整性证据和内容摘要，API 层不得从可变事实临时拼接引用。策略最小历史窗口使用单一共享常量 `MIN_HISTORY_ROWS=20`，backfill、计划和盘中判定不得各自维护阈值。

盘中 MarketInput 的 history 引用遵循以下优先级：

- 活动计划内标的继续使用计划冻结的 close history，保证计划条件不被盘中新数据悄悄改写。
- 无活动计划的持仓或展示标的使用截至 `history_cutoff_date` 的最新已验证本地 HistorySnapshot。
- 本地覆盖不足时保存明确的 degraded/stale/failed 质量，不省略 dataset 让下游猜测。

活动计划只控制买入侧资格，不再承担“是否存在 history”的职责。

## `display_only` 安全模式

`display_only` 是 `DecisionWorkflow` 的显式执行模式，不是客户端通用 `force`。服务端根据交易日历和请求的 `outside_session_mode=display_only` 决定实际模式；客户端不能要求在交易时段跳过风控后生成动作，也不能在非交易时段强制进入 decision。

后台 scheduler 永远发起 decision 模式，不使用 display-only。CLI 现有 force/reason 行为保持不变；新增展示刷新参数必须显式，不能把 force 重新解释为 display-only。

模式必须进入 intraday 的 run ID/幂等键、CaptureRun、MarketInputSnapshot、API 响应和审计。decision 继续使用交易时段三分钟周期；display-only 使用 `mode + effective_trade_date + requested_at 的 Asia/Shanghai 墙钟三分钟 bucket`，避免周末或盘前永久复用旧运行。这样 display-only 不会错误复用同周期 decision run，也不会被后续决策误认为交易时段输入。

CaptureRun 还保存请求 symbol scope 和由后端 10 分钟 intraday lease 计算的 `lease_expires_at`。run detail API 原样暴露这些字段，前端不得硬编码另一个超时。

新增 mode、dates、scope、lease 和 history completeness 字段必须有 SQLite migration、JSON payload 旧行默认和 repository round-trip。旧 CaptureRun 默认为既有工作流语义：intraday 为 decision，其余 mode 为空；缺少 dates/evidence 时保持不可验证而不伪造。服务升级和重启后，run list/detail 仍能读取旧行。

在读取策略、风控或通知之前设置硬门禁。display-only 允许写入：

- UniverseSnapshot。
- quote、minute bar、intraday strength 和 history snapshot/引用。
- 逐标的 DatasetQuality、CaptureRun、MarketInputSnapshot 和审计。

display-only 禁止写入：

- AccountSnapshot。
- TradingPlan。
- Recommendation。
- Notification、邮件 outbox 和人工反馈。

即使股票池中存在持仓，display-only 的 `recommendation_ids` 也必须恒为空，相关表行数必须保持不变。审计保存脱敏且限长的 manual reason、requested_at、mode、effective_trade_date 和 history_cutoff_date。

非交易时段完成后响应明确 `mode=display_only` 和“本次未生成交易建议”，不返回伪造的计划或账户结果。

## 盘后报价时间验证

公开 quote 有价格但没有经过契约验证的源时间时，默认仍是 partial，不能仅凭 `current_price` 视为可用于价格触发。

display-only 在收盘后、开盘前、周末或节假日可以复用现有 close workflow 的严格交叉验证规则：仅当本轮已固化的同一 `effective_trade_date` 前复权日 K 收盘价与 quote 最新价严格一致，并且观测发生在该会话收盘后，才以交易所会话收盘时间创建 partial 验证报价。价格不一致、日 K 缺失或午休时均不得制造 market data time。

该交叉验证只提升展示报价的可追溯性，不创建计划或建议。

## 分钟异常与同日缓存兜底

adapter 继续负责东方财富到新浪的双源 fallback。workflow 只在 routed provider 的获取调用抛出已识别的 provider capture error 时进入缓存兜底：

1. 查询同一 symbol、同一 `effective_trade_date` 的已保存分钟线。
2. 缓存存在时，用完整同日缓存重新计算分时强弱。
3. 无论缓存是否仍在 stale 阈值内，provider 本轮失败都至少标记 degraded，并保留清理后的 provider warning 和缓存 source provenance。
4. 缓存最后分钟超过配置的有效交易分钟阈值时标记 stale。
5. 缓存不存在时，minute_bar 和 intraday_strength 才标记 failed。

缓存反序列化、数据模型校验或强弱计算异常不是 provider fallback，必须按对应内部错误失败，不能被缓存分支吞掉。前一交易日、其他 symbol 或未来分钟数据严禁复用。

缓存兜底时 `rows_received=0`、`rows_written=0`，DatasetQuality 的 `actual_rows` 保存真实缓存行数，source 同时表达缓存事实来源和本轮 provider failure。quote 质量独立保存，分钟缓存成功不能提升失败或陈旧 quote。

## 数据质量与决策门禁

`DecisionSymbolInput` 增加 dataset-specific quality/usable 语义，至少区分：

- `quote_status` 与 `quote_usable`。
- `history_status` 与 `history_usable`。
- `intraday_status` 与 `intraday_usable`。
- `plan_status`。

quote usable 必须同时满足：

- status 为 complete/degraded，不能是 failed/stale；
- 价格字段有效；
- market data time 有效，或已通过上述严格收盘交叉验证。

overall 仍保存完整聚合质量用于展示和审计，但不能作为一个布尔值阻断所有价格动作。决策规则为：

- quote 不可用：保留 `quote_unavailable` 和“当前行情不可用，暂停价格触发型动作”。即使对象中残留价格，stale/failed 也不可触发。
- quote 可用、history 不可用：不得使用 `quote_unavailable`；保存独立 `history_unavailable` 原因，关闭依赖日线结构的条件。
- quote 和台账数量上下文可用但 history 缺失：只允许执行项目规约和当前代码已经定义、且不依赖 history 的硬风险；不得借本修复新增机械成本比例止损。没有现成可执行硬风险，或 `available_quantity=0` 时保持 HOLD，并准确说明 history/计划/可用数量约束。
- plan 缺失：非持仓不能 `buy/add`；持仓风险管理继续按可用数据执行。
- intraday 只有 status=complete、组件覆盖率达到规则要求且数据时间未 stale 时才可作为 `buy/add` 的分时确认。provider 失败后由缓存得到的 degraded/stale strength 只用于展示和风险说明，不能满足买入侧多因子门禁；依赖分时强弱的持仓条件也关闭，独立的有效 quote 硬风险不受影响。
- quote 与 history 都可用：继续现有计划、策略和硬性风控链。

回归测试必须覆盖 usable quote + missing history、stale quote + good history、failed quote + good history、两者均可用四个象限。

## API 契约与组合阶段

继续复用：

```text
POST /api/v1/service/workflows/backfill/run
POST /api/v1/service/workflows/intraday/run
GET  /api/v1/market/runs/{run_id}
```

人工行情刷新使用的 backfill 请求增加后端解析模式：

```json
{
  "as_of_mode": "latest_complete",
  "symbols": ["512480"]
}
```

`as_of_mode=latest_complete` 与显式 `trade_date` 互斥。API 使用 XSHG 日历和 close readiness 规则解析 `history_cutoff_date`，前端不得用浏览器自然日自行推算。未传 symbols 时仍使用当前启用股票池；显式 symbols 继续只能是该股票池的子集。现有 CLI 的显式交易日 backfill 语义保持不变。

intraday 请求增加：

```json
{
  "outside_session_mode": "display_only",
  "manual_reason": "market_page_refresh"
}
```

交易时段返回 `mode=decision`；非交易时段返回 `mode=display_only`。通用 WorkflowRunResponse 增加可空的 `mode`、`effective_trade_date` 和 `history_cutoff_date`：backfill 返回解析后的 history cutoff，intraday 返回实际模式和两个日期。普通未声明 display-only 的非交易时段 intraday 请求继续返回稳定 `422 workflow_outside_session`，避免悄悄改变现有 CLI/API 语义。

组合状态只存在于前端共享 coordinator，不伪装成单个 WorkflowRunResponse。每个 stage 单独保存：

- `workflow_type`。
- `run_id`。
- `status`。
- `warnings`。
- `reused`。
- `mode`（intraday stage）。

第一阶段成功、第二阶段失败时保留并显示部分成功。Stage 1 的业务终态 degraded/failed 不阻止 Stage 2 刷新报价与分时；认证、请求校验、数据库契约或无法获得可信 run 身份的 transport fatal 才停止组合流程。

每个阶段的 `409 workflow_in_progress` 都必须携带精确 run ID。coordinator 轮询终态后校验 run 的 requested symbol scope；若 active backfill 没覆盖本次 scope，只对 missing symbols 有界重提一次，仍不足则显示部分完成而不无限重试。intraday 仍核对本轮统一股票池覆盖。

## 前端交互与运行跟随

行情页标题区增加带刷新图标的“获取行情”按钮。按钮使用稳定尺寸，执行期间显示三个阶段：

1. `正在补齐 K 线`。
2. `正在获取报价与分时`。
3. `正在刷新页面数据`。

共享 workflow coordinator 被行情页、建议页和监控页复用。它负责：

- 正常同步响应的阶段推进。
- `409 workflow_in_progress` 转为“已有任务正在运行”，读取 `details.run_id`。
- 每 2 秒轮询精确 run 详情，短暂 404 继续等待；使用 run detail 返回的 `lease_expires_at`/`retry_after` 决定等待上限，达到上限后提示“任务仍未结束，请在监控页查看”，不得宣称失败或成功。
- 页面卸载或用户取消时终止前端轮询，但不终止后台工作流。
- terminal succeeded 时显示完成；degraded 时显示部分可用和 warnings；failed 时才显示失败。
- 将 WorkflowRunResponse 的 `success` 与 run detail 的 `succeeded` 归一为同一成功终态；未知状态保守停止并显示契约错误。
- 终态后 invalidate market symbols/overview/daily/minute/strength/trace、recommendations、notifications 和 market runs 查询。

非交易时段 display-only 完成提示“行情展示已刷新，本次未生成交易建议”。使用同日缓存时提示“已使用当日缓存，数据部分可用”。不得再把 409、422、degraded 或运行中状态统一映射成“盘中决策工作流运行失败”。

## 建议当前视图与历史视图

建议 API 增加显式 `view=current|history`。为兼容现有 API/CLI，无 `view` 参数时保持旧 history 数据、旧 Recommendation item 结构和分页语义；显式传 view 时才返回 linked projection。前端建议页显式请求 `current` 作为产品默认视图：

- current 使用 SQL 按 symbol 分组，以 `created_at DESC, rowid DESC` 稳定选择最新建议，`total` 是分组后的标的数。
- history 返回完整周期历史，保留现有稳定分页，`total` 是建议总数。

建议列表不修改持久化的不可变 Recommendation payload。API 使用投影 DTO：

```json
{
  "recommendation": {"recommendation_id": "..."},
  "notification": {"notification_id": "...", "status": "unread"}
}
```

`notification` 在尚未投递或历史迁移无法建立 link 时为 null。显式 current 和 history 两种 view 都返回同一 `RecommendationListItem` 结构，分页 total 仍分别使用当前分组数和历史总数。无 view 的 legacy 响应不包装 DTO；详情和 trace 继续按实际 recommendation ID 返回原始 Recommendation。

前端使用分段控件切换“当前状态 / 历史记录”，从 DTO 的 notification 投影显示处理状态。默认当前状态每个标的只显示一条。不能在前端先分页再分组，也不能为了当前视图删除历史建议。

## 通知去重与建议关联

notification canonical key 使用：

```text
Asia/Shanghai 交易日
+ symbol
+ final action
+ plan ID + plan version（无计划分别使用稳定 no-plan/no-version）
+ recommendation.condition_fingerprint
```

去重键移除 run ID 和三分钟周期。canonical fingerprint 使用明确的 `fingerprint_version=v2`；新 Recommendation 在创建身份时保存 v2 fingerprint。它覆盖触发条件、风险、失效条件、仓位约束和影响裁决的稳定证券制度字段，排除 data_time、fetched_at、metadata_checked_at 等纯时间元数据。dispatcher 直接复用该版本化字段，不再自行维护另一个算法。

数据库唯一索引继续作为并发兜底，重复 dispatch 不能重置既有通知的 unread/read/feedback 状态。

每个周期仍会产生新 recommendation ID。新增显式 recommendation-notification link：

- 新条件创建 notification 并写 link。
- 同条件命中既有 notification 时不创建新通知，但仍把本周期 recommendation ID 链接到该 notification ID。
- 不更新旧 notification 的原始 recommendation ID，不破坏首次通知审计。
- 反馈使用 link 找到 canonical notification 并更新处理状态；建议列表响应返回本页 recommendation 对应的 notification link/status，前端不再只按 notification 原始 recommendation ID 猜测。

canonical 身份通过独立 `notification_canonical_groups` 映射表表达：`canonical_key` 主键、`notification_id` 非空唯一外键、`created_at`，外键删除策略为 RESTRICT。`recommendation_notification_links` 使用 recommendation ID 主键、notification ID 非空外键和 canonical key 非空外键，均为 RESTRICT，并为 notification ID 建索引。API、迁移和查询不得解析 dedup key 字符串判断 canonical。

创建顺序在一个事务中固定为 notification/audit -> canonical group -> recommendation link。并发唯一冲突时回滚本事务，重新读取已提交 group，再单独幂等写 link；notification dedup index、canonical key 主键和 recommendation link 主键共同收敛并发。

通知 API 增加 `GET /notifications?view=current|history`。为兼容旧调用，无参数默认 history；前端复盘/仪表盘显式请求 current。current 列表和未读计数是“canonical recommendation notifications UNION 全部 system_alert”；行情扫描器的逐标未读数和 CLI unread 也必须调用同一 canonical repository/service 查询。系统告警沿用自身去重语义且不进入建议条件分组。history 返回全部原始建议通知和系统告警。

已有重复建议通知不删除。兼容迁移从保存的 Recommendation payload 重新计算 v2 fingerprint，不改写旧 Recommendation identity。旧 fingerprint 为 NULL/旧版本、包含易变元数据时不能直接作为 canonical；payload 损坏或 recommendation 缺失时保守地让该通知独立成组并记录脱敏 warning，绝不猜测折叠。

同一组存在多种处理状态时按 `feedback_recorded > read > unread` 选择 canonical，同级再选最新，避免已处理事实重新变成未读。迁移写入 canonical mapping 和 recommendation links，其他旧通知保留在 history。迁移必须单事务、可幂等重跑；失败时回滚 mapping、dedup 更新和 links，不能留下半迁移状态，也不得改写旧通知的处理状态或审计引用。

动作、风险、失效条件、仓位约束、证券制度、计划版本或 canonical condition fingerprint 发生实质变化时必须生成新通知；跨上海交易日即使条件相同也生成新通知。

## 错误处理

- `409 workflow_in_progress` 是可跟随状态，不是失败。
- 未声明 display-only 的非交易时段请求保持 `422 workflow_outside_session`。
- display-only 仍遇到无有效交易日、数据库契约错误或所有关键展示数据失败时返回稳定、脱敏错误。
- provider 单标的失败继续逐标的降级；数据库引用、快照成员哈希或模型契约失败终止对应工作流。
- 第一阶段成功、第二阶段失败不回滚第一阶段，也不重新调用已成功 provider。
- API、审计、warning、通知和前端不得泄露 token、URL 中凭据、数据库路径或第三方原始响应。

## TDD 与测试矩阵

### 日 K 与 History

- 新增启用标的没有 K 线时，人工 backfill 补齐并固化 HistorySnapshot。
- intraday 仅固化引用，或人工刷新复用同一已完成 cutoff/scope 时，本地覆盖完整不调用 provider；有缺口时只补缺口。
- 人工 latest-complete 第一次命中新 cutoff/scope 时，即使本地覆盖完整也执行最近 5 日 correction。
- 盘中、午休、收盘后未就绪、收盘后已就绪、开盘前和周末选择正确 history cutoff 和幂等键。
- 新上市实际区间完整但少于 250 根时为 degraded/usable；不足最小窗口时不可用于结构判断。
- 本地短缓存没有 listing date 或 provider 完整窗口证据时不可验证，必须补证或保持 unusable。
- 人工 latest-complete 首次请求保留最近 5 日 correction；同 scope 后续幂等复用；显式 CLI/close correction 不回归。
- 无活动计划但本地 history 有效时，MarketInput 包含不可变引用。
- 有活动计划时继续引用计划冻结 history。

### Display-only 隔离

- 午休、收盘后、开盘前和周末选择正确 effective trade date。
- display-only 有持仓时也不调用账户估值、策略、风控、建议和通知。
- CaptureRun、MarketInput 和审计保存 mode/dates/reason，recommendation_ids 恒空。
- recommendations、notifications、account_snapshots、trading_plans 和 email outbox 行数不变，capture/audit 增加。
- 盘后无源时间 quote 只有通过同日 K 收盘价严格交叉验证才获得市场时间。

### 质量四象限

- usable quote + missing history：不是 quote_unavailable，历史结构动作关闭。
- stale quote + good history：价格触发暂停。
- failed quote + good history：价格触发暂停。
- usable quote + usable history：现有决策链不回归。
- 无计划持仓仍可执行不依赖历史的硬风险；非持仓不能 buy/add。
- provider 失败后的 cached degraded/stale strength 不能充当 buy/add 分时确认。

### 分钟缓存

- provider 抛错 + 同日缓存：计算 strength，至少 degraded，保存 warning、真实缓存行数和零接收/写入行数。
- 同日缓存超过有效交易分钟阈值：minute 和 strength 为 stale。
- provider 抛错 + 无同日缓存：failed。
- 前一交易日或其他 symbol 缓存：不得复用。
- provider 成功、返回空列表、provider 获取异常、缓存校验异常和强弱计算异常分别保持正确语义。

### 建议与通知

- 两个三分钟周期同条件：两条建议、一个 canonical 通知、两条 recommendation-notification links。
- risk、invalid_if、constraint、instrument rule、plan version、action 或 fingerprint 任一实质变化：新通知。
- plan ID 改变但 plan version 相同：新通知。
- 纯 data_time/fetched_at/metadata_checked_at 变化：不创建新通知。
- 跨上海交易日相同条件：新通知。
- current 建议按 symbol 稳定分页，history 保留全部。
- 旧重复通知迁移后 current/未读计数折叠，history 和审计仍完整。
- canonical mapping 迁移可幂等重跑，并发唯一约束有效，注入失败时完整回滚。
- 迁移前后 system_alert 都出现在 current/history，并按原状态计入未读数。
- 对最新建议记录反馈时，通过 link 更新 canonical notification。

### API 与前端

- backfill/intraday 两阶段分别显示同步成功、degraded、failed、reused 和 409 跟随。
- 409 精确轮询 run 详情，短暂 404、轮询上限和页面卸载行为确定。
- 第一阶段成功、第二阶段失败显示部分成功。
- terminal 后刷新所有相关 query；degraded 不显示红色失败。
- 非交易时段显示“未生成交易建议”。
- 建议“当前状态 / 历史记录”和通知处理状态关联正确。

## 文档同步

实现时同步更新：

- `docs/project-spec.md`：人工组合刷新、display-only 和当前/历史展示。
- `docs/trading-policy.md`：非交易时段禁止决策写入，dataset-specific 风控门禁。
- `docs/data-sources.md`：history cutoff、local-first 固化、同日分钟缓存和盘后 quote 验证。
- `docs/recommendation-contract.md`：current/history API、canonical fingerprint、通知 link 和跨周期去重。
- `docs/api.md`：intraday 参数/响应、运行跟随、建议和通知 view 语义。

## 验收标准

- 当日新增且已验证的标的点击“获取行情”后可以读取 K 线；新上市短历史被诚实标记为降级而非失败。
- 无活动计划时，有效本地日 K 进入盘中 MarketInput；可用 quote 不再显示“当前行情不可用”。
- 两个分钟来源瞬时失败时，同日缓存继续提供可追溯的 degraded/stale 分时强弱；跨日缓存不复用。
- 午休、收盘后、盘前和非交易日的人工刷新只更新展示数据，所有决策及账户派生表行数不变。
- 与调度器并发点击时显示实际 run ID 和进度，后台终态成功或降级后自动刷新，不立即报错。
- 默认建议页每个标的只有最新状态；历史页仍能查看全部三分钟周期记录。
- 相同条件 HOLD 跨周期只产生一个 current 通知和一个未读计数，最新建议仍正确关联其处理状态。
- 后端完整测试、Node 24 前端测试和构建、Playwright 桌面/移动关键流程及真实服务冒烟验证均有 fresh 结果。
- 所有实现阶段经过独立后台 agent 复核；任何未运行或未通过的验证明确披露。
