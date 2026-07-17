# 交易建议消息契约

## 1. 目的

交易建议消息契约定义策略、风控、日志和推送之间的统一接口。任何建议都必须可解释、可追溯，并能被人工复核。

## 2. 必填字段

每条建议至少包含以下字段：

```json
{
  "symbol": "600000",
  "name": "示例股票",
  "instrument": {
    "symbol": "600000",
    "name": "示例股票",
    "exchange": "SH",
    "instrument_type": "a_share",
    "settlement_cycle": "t1",
    "price_limit_ratio": 0.1,
    "metadata_source": "akshare_a_share_directory",
    "metadata_checked_at": "2026-07-07T08:00:00+08:00",
    "rule_version": "instrument-rules-v1",
    "warnings": []
  },
  "action": "watch",
  "confidence": "medium",
  "position_context": {
    "source": "manual_ledger",
    "ledger_updated_at": "2026-07-07T09:00:00+08:00",
    "cost_price": 9.5,
    "quantity": 1000,
    "available_quantity": 1000
  },
  "account_context": {
    "source": "manual_cash_account",
    "cash_balance": 48000,
    "net_principal": 50000,
    "market_value": 10500,
    "total_assets": 58500,
    "position_ratio": 0.18,
    "account_snapshot_time": "2026-07-07T10:30:00+08:00"
  },
  "price_context": {
    "current_price": 10.0,
    "change_pct": 1.2,
    "key_levels": {
      "support": 9.7,
      "resistance": 10.4,
      "stop_loss": 9.3
    }
  },
  "reason": ["站上短期均线", "成交额放大", "资金流转正"],
  "risk": {
    "position_limit": "单票不超过配置上限",
    "invalid_if": ["跌破 9.7", "资金流重新转负"],
    "notes": ["行情数据可能延迟"]
  },
  "valid_until": "2026-07-07T15:00:00+08:00",
  "data_time": "2026-07-07T10:30:00+08:00",
  "fetched_at": "2026-07-07T10:30:03+08:00",
  "run_id": "intraday-20260707-1030",
  "market_input_snapshot_id": 101,
  "plan_id": "plan-20260707-v2",
  "data_references": {
    "ledger": {"status": "complete", "updated_at": "2026-07-07T09:00:00+08:00"},
    "account": {"status": "complete", "snapshot_id": 88},
    "quote": {"status": "complete", "snapshot_id": 501},
    "history": {"status": "complete", "snapshot_id": 301},
    "money_flow": {"status": "degraded", "snapshot_id": 201},
    "intraday": {"status": "complete", "snapshot_id": 401},
    "plan": {"status": "active", "plan_id": "plan-20260707-v2"}
  },
  "data_quality": {
    "overall": "degraded",
    "warnings": ["资金流最近一日暂缺"]
  },
  "position_constraint": {
    "suggested_range": [0.1, 0.15],
    "effective_cap": 0.15
  }
}
```

## 3. 字段说明

### 证券身份与交易制度

顶层 `symbol` 使用 ASCII 六位代码，`name` 是建议展示名称；`instrument` 对象保存后端已验证的 `symbol`、`name`、`exchange`、`instrument_type`、`settlement_cycle`、`price_limit_ratio`、`metadata_source`、`metadata_checked_at`、规则版本和 warnings，并随建议保存。前端不得根据代码或名称补算证券类型、T+0/T+1 或涨跌停比例。

`instrument_type` 为 `a_share/etf/unknown`，`settlement_cycle` 为 `t0/t1/unknown`。A 股固定 T+1；ETF 只根据交易所产品类别的明确规则映射。未知证券或未知交易制度不得产生 `buy/add/sell/reduce`；未知持仓降级为保守 `hold` 并要求人工复核。

### `action`

建议动作。可选值为：

- `buy`
- `sell`
- `add`
- `reduce`
- `hold`
- `watch`
- `avoid`

### `confidence`

信号置信度。可选值为：

- `low`
- `medium`
- `high`

置信度不是收益承诺，只表示当前规则条件的匹配程度。

### `position_context`

持仓上下文。持仓相关建议必须从手动持仓台账读取成本价、持仓数量、可用数量和台账更新时间。本地观察池中尚未持仓的证券可以显式标记为空仓。

若成本价、持仓数量或可用数量缺失，持仓相关建议应降级为人工复核或暂停。

### `account_context`

资金上下文。涉及买入、加仓、减仓或仓位约束的建议，应包含手动资金账户和账户估值快照中的现金余额、净本金、持仓市值、总资产、仓位比例和快照时间。

现金余额是首版可用买入资金的基础口径，净本金来自手动资金账户的累计转入和累计转出。不得从 AkShare 行情、东方财富模拟组合或其他外部市场数据反推真实资金状态。

当账户估值因为行情缺失处于部分可用或不可用状态时，建议必须显式说明资金上下文的数据缺口，并降级或禁止依赖完整仓位比例的买入动作。

### `price_context`

价格上下文，包括当前价、涨跌幅、支撑位、压力位、止损位等市场信息。若某字段无法获得，应显式标记缺失，不得伪造。

### `reason`

触发原因列表。原因必须可解释，且应能回溯到具体数据或规则。

### `risk`

风险信息，包括仓位上限、失效条件、止损条件、数据风险、实际 T+0/T+1 制度、手动可用数量和未知规则降级等。

### `valid_until`

建议有效期。盘中建议通常不应超过当日收盘。收盘计划中的建议应明确适用的交易日。

### `data_time`

建议使用的数据时间戳。推送时间不能替代数据时间。

正常建议的 `data_quality.data_time_source=market`，此时 `data_time` 是实际市场数据时间。若未知元数据持仓没有任何可用行情时间，为保证仍能输出保守 `hold`，允许以证券元数据核验时间作为唯一输入时间，并明确标记 `data_quality.data_time_source=instrument_metadata`、`price_context.market_data_time=null` 和人工复核警告；不得使用台账更新时间、获取时间或建议生成时间伪装成行情时间。

### `fetched_at`

本轮固化市场输入的系统获取时间。它与 `data_time` 和建议 `created_at` 分别表示获取时间、市场数据时间和建议生成时间，三者不能互相替代。

### 工作流和数据引用

`run_id`、`market_input_snapshot_id` 和 `plan_id` 将建议连接到实际执行轮次、固化的行情输入和活动计划。`data_references` 必须包含 `ledger`、`account`、`quote`、`history`、`money_flow`、`intraday` 和 `plan`；每项至少包含稳定 `status`，可用时附实际引用 ID、数据时间或覆盖区间、获取时间和来源，不可用时必须明确 `missing/failed/stale`，不能省略后让调用方猜测。ETF 的 `money_flow.status=not_applicable` 是额外的合法数据集状态，不表示失败、缺失或成功采集，也不能作为资金流确认。

`data_quality` 保存总体质量、逐数据集状态、warnings 和实际生效的 stale/降级语义。总体质量仍只使用 `complete/degraded/failed/stale`；ETF 合法的资金流不适用本身不降低总体质量。`position_constraint` 保存建议仓位区间、建议数量以及经过现金、单票、总仓位、证券交易制度、手动可用数量和流动性裁决后真正生效的上限。所有这些字段由后端生成，前端不得补算。

## 4. 首版实现约束

首版建议模型必须校验 `valid_until` 和 `data_time` 为带时区时间。`reason` 必须非空，且不得包含确定性收益或保证正收益表述。

`buy`、`add`、`hold`、`watch` 类型建议必须包含非空 `risk.invalid_if`。最终建议动作必须以风控结果为准；当风控将策略信号降级时，建议中的 `action` 使用降级后的动作，同时保留原始策略原因和风控说明用于复核。

`buy/add` 必须以适用于当日的活动收盘计划为硬门禁：标的和动作在计划中被允许、命中机器条件、获得至少两个独立有效因子确认并通过硬性风控。A 股资金流只能确认或过滤，不能独立触发动作；ETF 资金流不适用，不提供确认也不作为失败惩罚，仍需日线结构和盘中强弱等两个独立有效因子。计划外机会和无活动计划的非持仓标的只能 `watch/avoid`。

盘中展示采集可以为 `plan_enabled=true` 且元数据已验证的计划外观察标的保存报价、分钟线和分时强弱，但展示采集资格不等于建议资格。这类标的不进入策略、风控、建议或通知，不能仅因行情完整或分时强弱为 strong 生成 `watch/avoid`，更不能生成 `buy/add`。

持仓风险管理不受买入侧计划门禁阻断。无活动计划或原计划条件失效时，持仓新出现的止损、回撤、跌破结构或仓位超限仍可生成 `sell/reduce/avoid`；建议必须保存原计划条件、覆盖原因和风控裁决。关键报价、成本、数量、可用数量或证券交易制度缺失时不得猜测，必须降级为保守 `hold` 和人工复核。手动 `available_quantity=0` 时，即使证券是 T+0 ETF 也禁止 `sell/reduce`。

每轮盘中工作流重新读取手动台账和资金账户。计划中的数量和账户上下文只是历史引用，不能覆盖最新权威台账。

### 建议去重

去重键至少包含交易日、3 分钟周期、标的、最终动作、计划版本和条件指纹。条件指纹覆盖触发规则、风险、失效条件和仓位约束。相同输入和条件的幂等重跑复用已有建议；动作、风险、失效条件、数量、仓位上限或计划版本发生实质变化时允许生成新建议。

条件指纹当前版本为 v2。它包含 `reason`、`risk`、`position_constraint`、`condition_context` 及影响裁决的稳定证券制度字段，排除 `data_time`、`fetched_at`、`metadata_checked_at`、展示名称、来源和 warning 等易变展示元数据。Recommendation 仍按交易日和 3 分钟周期保存完整审计历史；通知 canonical key 则移除周期，使用上海交易日、标的、最终动作、计划 ID/版本和 v2 条件指纹，使跨周期相同条件只产生一条当前通知。

## 5. HTTP API 契约

建议 API 只读取统一 `DecisionWorkflow` 已经保存的建议：

```text
GET /api/v1/recommendations?page=1&page_size=20
GET /api/v1/recommendations?view=current&page=1&page_size=20
GET /api/v1/recommendations?view=history&page=1&page_size=20
GET /api/v1/recommendations/{recommendation_id}
GET /api/v1/recommendations/{recommendation_id}/trace
```

列表统一返回分页封装：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 20
}
```

无 `view` 时保持旧客户端契约，`items` 直接包含本文定义的 Recommendation，且返回完整历史。显式 `view=current|history` 时，`items` 统一为 `{"recommendation": {...}, "notification": {"notification_id": "...", "status": "unread"}}`；尚未投递或无法建立 link 时 `notification=null`。current 在 SQL 中按 symbol 以 Recommendation 数据库 `created_at DESC, rowid DESC` 选择最新一条，再执行稳定排序和分页，`total` 是 symbol 数；显式 history 保留完整历史、旧排序和建议总数。对于非持仓买入侧，最新计划不存在时返回或降级为稳定的计划门禁结果；计划不可消费时必须保留 `plan_id`、`status` 和 `valid_until`。持仓风险例外不能被计划错误整体阻断。详情接口按 `recommendation_id` 返回原始单条建议，trace 接口解析实际输入和审计引用。

旧 `POST /api/v1/recommendations/scan` 已退役，认证请求固定返回 HTTP `410`、错误码 `recommendation_scan_retired` 和盘中工作流替代地址，不采集数据也不写建议。旧 `qt recommendations scan` 同样以非零状态退出；手动生成盘中建议只能使用认证的 `POST /api/v1/service/workflows/intraday/run` 或 `qt workflow intraday`，两者与后台调度共享 `DecisionWorkflow`。

## 6. 通知与反馈状态

通知摘要是建议推送和复盘之间的本地状态记录。通知必须包含建议 ID、股票、动作、置信度、关键价、理由、风险、数据时间、审计 ID 和处理状态。

通知处理状态首版为：

- `unread`：已生成通知，尚未处理。
- `read`：用户或后续界面已标记读取。
- `feedback_recorded`：已记录关联建议的人工执行反馈。

每个 Recommendation 通过显式 link 关联 canonical notification；通知保留首次创建时的原始 Recommendation ID 和审计引用，不随新周期覆盖。人工执行反馈字段为 `recommendation_id`、`executed`、`execution_price`、`execution_quantity`、`note` 和 `created_at`。反馈写入后优先通过 link 把 canonical notification 标记为 `feedback_recorded`；旧数据没有 link 时，只按完全相同的通知原始 Recommendation ID 保守回退，不按标的、动作或相似条件猜测。反馈不得修改手动持仓台账、手动资金账户、现金余额、净本金或账户快照，只用于复盘和策略改进，不代表系统确认真实成交。

通知和审计读取路由是稳定 API：通知支持列表、未读数和标记已读，审计支持列表和按 ID 查询；所有列表统一返回 `{items,total,page,page_size}`。`GET /notifications?view=current|history` 显式选择当前或历史视图，无 `view` 时为兼容旧调用固定返回 history。current 和未读数使用“canonical Recommendation 通知加全部 `system_alert`”集合；history 返回全部原始通知。通知去重键包含上海交易日、标的、动作、计划 ID/版本和 v2 条件指纹；相同条件不重复创建，跨日或实质变化允许新通知。

`buy/add/sell/reduce` 在本地通知成功后立即写入邮件 outbox；`hold/watch/avoid` 只在收盘计划发布后进入同一活动计划版本的一封每日摘要。关键工作流故障先创建数据库通知并投射到 Web/API、控制台和 JSONL，SMTP 可用时再进入邮件 outbox；SMTP 不可用不得压制本地告警。邮件达到最大尝试次数成为 `dead` 时生成去重的本地系统告警，同样可在 Web 查看并写控制台和 JSONL。邮件失败独立重试，不得回滚建议、通知或重跑 `DecisionWorkflow`。通知、邮件和审计中的错误只能保存经过清理的安全摘要。

## 7. 日志要求

结构化日志应记录：

- 建议 ID。
- `run_id`、`market_input_snapshot_id` 和 `plan_id`。
- 输入数据快照引用。
- 手动持仓台账快照引用。
- 策略信号。
- 风控结果。
- 最终建议。
- 推送渠道和推送状态。
- 用户是否执行以及人工反馈。
- 通知去重键、邮件 delivery ID 和安全投递状态。

## 8. 推送格式

本地控制台和消息渠道可以使用更短的展示格式，但不得丢失以下信息：

- 股票。
- 动作。
- 置信度。
- 关键价格。
- 理由。
- 风险和失效条件。
- 数据时间。

若展示格式无法容纳完整详情，应写入日志并在推送中给出日志引用或建议 ID。
