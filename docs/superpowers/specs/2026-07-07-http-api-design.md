# 后台 HTTP API 设计

## 背景

本项目是个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不自动真实下单，不控制真实交易客户端，不读取真实交易凭据，也不得把系统输出描述为确定性收益或保证正收益。

当前仓库已经实现手动持仓台账、手动资金账户、账户估值、AkShare 行情 provider、SQLite 存储、CLI 和调试版后台账户快照 runner。下一阶段需要在已有能力之上开发后台 HTTP API，为后续前端对接提供稳定的数据和操作接口。

本设计只覆盖后台 API 和统一后台服务入口，不实现前端页面。

## 目标

- 引入 FastAPI，提供 `/api/v1` HTTP API。
- 让 `qt service run` 成为统一后台服务入口：同时运行 HTTP API 和可控调度器。
- API、CLI 和调度任务共享现有 service、repository、Pydantic model 和 market provider。
- 通过 HTTP API 完整覆盖现有 CLI 的持仓台账、资金账户、账户估值和后台服务控制能力。
- 采用单用户访问密码认证，不引入多用户、租户或角色权限。
- 在没有访问密码时允许服务启动，但进入 setup required 状态，提示用户设置访问密码并将密码哈希落库。
- 提供持久化调度开关：通过 API 启动调度后，下次服务启动应自动恢复调度。
- 统一 API 错误响应格式，便于前端稳定处理。
- 保持所有写操作语义为“维护手动台账或手动资金账户”，不代表真实券商交易。

## 非目标

- 不实现自动真实下单。
- 不模拟点击真实交易客户端。
- 不读取、保存或提交真实账户密码、token、cookie、API key。
- 不实现前端页面、移动端或复杂可视化看板。
- 不实现多用户数据隔离、用户注册、角色权限或租户管理。
- 不实现策略建议、人工反馈、模拟组合操作或远程推送 API。
- 不把 HTTP API 写操作解释为真实交易行为。
- 不把东方财富模拟组合、AkShare 或其他外部市场数据作为真实持仓或真实资金来源。

## 推荐方案

采用“FastAPI API 层 + 持久化调度状态 + 共享核心 service”的方案。

API 层只负责协议、认证、请求响应模型、错误映射和路由组织。持仓、资金、账户估值和行情获取必须继续调用现有 application service 和 repository，不为 HTTP 入口维护第二套业务逻辑。

统一后台服务入口由 `qt service run` 启动。服务启动时加载配置、执行 SQLite migration、创建 FastAPI app 和调度管理器，并读取调度持久化状态。如果调度状态为开启，则恢复周期任务；是否启动后立即执行一次快照由配置控制，默认开启。

该方案比直接重构成大型后台应用壳更克制，能满足前端接口基础和调度持久化需求，同时降低对现有 CLI 和服务测试的影响。

## 模块边界

新增或扩展模块按以下职责设计：

- `api.app`：创建 FastAPI app，注册路由、中间件和异常处理。
- `api.auth`：访问密码认证、token 签发与校验、setup required 状态。
- `api.errors`：统一错误模型和领域异常到 HTTP 错误的映射。
- `api.dependencies`：数据库连接、migration、service factory、market provider factory。
- `api.routes.positions`：持仓台账 HTTP 资源接口。
- `api.routes.cash`：资金账户 HTTP 资源接口。
- `api.routes.account`：账户快照 HTTP 资源接口。
- `api.routes.service`：服务状态、调度控制和手动运行接口。
- `runtime.scheduler`：管理周期快照任务、启动、停止、恢复和运行状态。
- `storage`：新增认证状态、调度状态和快照 latest 查询所需 repository 或表。
- `cli`：扩展 `qt service run`，运行统一后台服务。

核心约束：

- API 不直接读写 SQLite 表，必须通过 repository 或明确的 storage abstraction。
- API 不直接实现持仓、资金或账户估值业务规则，必须调用既有 service。
- CLI 和 API 的行为差异只允许出现在协议层、格式化层和认证层。
- 后台调度任务不得调用持仓台账或资金账户写接口。

## 认证设计

首版采用单用户访问密码 + 短期 Bearer token。

### 认证来源

访问密码不保存明文。认证配置按以下优先级处理：

1. SQLite 中已落库的访问密码哈希。
2. 环境变量或未入库本地配置中的启动密码，例如 `QT_API_ACCESS_PASSWORD`。
3. 两者都不存在时，服务进入 setup required 状态。

如果环境变量提供了访问密码，而 SQLite 尚未保存密码哈希，服务可以启动并提示用户通过设置接口将密码哈希落库。实现阶段可以选择在首次成功登录后提示落库，或提供显式 `setup-password` 接口完成落库。

### Setup Required 状态

当没有可用访问密码时：

- 服务必须允许启动，不能因为缺少访问密码直接退出。
- 服务状态接口应明确返回 `auth_status=setup_required`。
- 控制台启动日志应提示用户设置访问密码，并说明建议将密码哈希落库。
- 业务接口默认不应在无认证状态下开放，避免服务绑定到非本机地址时裸奔。
- 允许访问最小集合接口：健康检查、服务状态、认证 setup 接口。
- setup 接口只能在未设置密码时使用；一旦密码已落库，重复 setup 应失败。
- setup 接口接收明文密码但只保存密码哈希，不保存明文。

如果实现阶段需要更强保护，可以在 setup required 状态下要求服务默认监听 `127.0.0.1`，或在绑定非本机地址且未设置密码时输出高风险警告。本设计不要求实现复杂的多步骤安装向导。

### 登录与 Token

接口：

```text
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET /api/v1/auth/me
POST /api/v1/auth/setup-password
```

`POST /api/v1/auth/login` 请求：

```json
{
  "password": "example"
}
```

成功响应：

```json
{
  "access_token": "token",
  "token_type": "bearer",
  "expires_at": "2026-07-07T10:30:00+08:00"
}
```

规则：

- 业务接口要求 `Authorization: Bearer <token>`。
- token 短期有效，TTL 由 `QT_API_TOKEN_TTL_SECONDS` 配置。
- token 签名密钥来自环境变量、未入库配置，或首次启动生成并落库的本地随机密钥。
- `GET /api/v1/auth/me` 返回固定单用户身份摘要，例如 `{ "user": "local" }`。
- `logout` 首版可以只由前端清除 token；如实现 token 撤销表，也不得影响核心业务模型。

## API 约定

- API 前缀为 `/api/v1`。
- 请求和响应字段使用 `snake_case`。
- 时间使用带时区 ISO 8601 字符串。
- 金额和比例沿用现有 Pydantic 模型口径，不在 API 层做展示格式化。
- 所有业务接口需要认证，setup required 允许的最小接口除外。
- 写接口返回变更后的资源或操作结果摘要。
- 删除接口成功后返回空响应或被删除资源摘要，具体实现保持一致即可。

## 统一错误格式

所有 API 错误统一返回：

```json
{
  "error": {
    "code": "position_not_found",
    "message": "position not found",
    "details": {
      "symbol": "600000"
    }
  }
}
```

FastAPI/Pydantic 校验错误也必须转换为该格式，`details` 保留字段路径和错误原因。

建议错误码：

- `unauthorized`
- `forbidden`
- `auth_setup_required`
- `auth_already_configured`
- `validation_error`
- `position_not_found`
- `position_conflict`
- `cash_account_not_initialized`
- `cash_account_already_initialized`
- `cash_transfer_invalid`
- `snapshot_not_found`
- `market_data_unavailable`
- `scheduler_error`
- `internal_error`

错误响应不得包含访问密码、token、cookie、API key、原始第三方凭据或敏感本地路径。

## 接口设计

### 认证

```text
POST /api/v1/auth/setup-password
POST /api/v1/auth/login
POST /api/v1/auth/logout
GET /api/v1/auth/me
```

`setup-password` 仅在没有访问密码哈希时可用。它将密码哈希落库，并让服务从 setup required 状态转为 configured 状态。

### 持仓台账

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

规则：

- 单条输入复用 `PositionInput` 语义。
- `symbol` 使用 6 位 A 股代码。
- 更新接口路径中的 `symbol` 必须与 body 中的 `symbol` 一致，或 body 不重复传 `symbol`。
- JSON 批量导入必须先校验所有行；任意一行失败则整次失败，不允许部分写入。
- CSV 上传和下载字段与现有 CLI 保持一致：`symbol,name,quantity,available_quantity,cost_price,opened_at,note`。
- 删除代表删除手动台账记录，不代表真实卖出。

### 资金账户

```text
GET /api/v1/cash/account
POST /api/v1/cash/account
POST /api/v1/cash/transfers
POST /api/v1/cash/adjustments
GET /api/v1/cash/transactions?limit=20
```

规则：

- `POST /cash/account` 初始化本金，只能执行一次。
- `POST /cash/transfers` 使用 `type=transfer_in|transfer_out` 表达模拟银证转入或转出。
- `POST /cash/adjustments` 用于现金校准，必须提供 `note`。
- 所有资金写操作只修改手动资金账户，不代表真实银行或券商账户发生交易。
- 转出不能超过当前现金，也不能导致净本金小于 0。
- 资金流水返回 `cash_before`、`cash_after`、类型、金额、时间和备注。

### 账户估值

```text
GET /api/v1/account/snapshot
POST /api/v1/account/snapshots
GET /api/v1/account/snapshots/latest
```

规则：

- `GET /account/snapshot` 默认返回最近持久化快照。
- `GET /account/snapshot?fresh=true` 生成并保存新快照。
- `POST /account/snapshots` 显式生成并保存新快照。
- `GET /account/snapshots/latest` 只读取最近持久化快照。
- 如果没有最近快照，返回 `snapshot_not_found`。
- 行情失败时仍返回带 `status` 和 `warnings` 的账户快照，不把缺失行情伪装成完整估值。
- 账户汇总字段必须遵守既有 `AccountSnapshot` 的 partial/unavailable 口径。

### 服务与调度

```text
GET /api/v1/service/status
POST /api/v1/service/scheduler/start
POST /api/v1/service/scheduler/stop
POST /api/v1/service/run-once
```

`GET /service/status` 返回：

- 认证状态：`configured` 或 `setup_required`。
- 调度期望状态：持久化的 enabled。
- 调度实际状态：当前进程是否已启动周期任务。
- 当前 interval、timezone、run_on_start 配置。
- 下次运行时间。
- 最近运行开始时间、结束时间、状态、原因。
- 最近快照 ID 和快照状态。
- 最近错误摘要。

`POST /service/scheduler/start`：

- 持久化 `enabled=true`。
- 启动当前进程周期任务。
- 如果已启动，应保持幂等并返回当前状态。

`POST /service/scheduler/stop`：

- 持久化 `enabled=false`。
- 停止当前进程周期任务。
- 如果已停止，应保持幂等并返回当前状态。

`POST /service/run-once`：

- 使用同一个账户快照任务函数执行一次。
- `reason=manual_api`。
- 保存快照和最近运行结果。
- 不改变调度 enabled 状态。

## 调度恢复设计

服务启动流程：

1. 加载配置。
2. 打开 SQLite 并执行 migration。
3. 创建认证 store、业务 repository、service factory 和 market provider factory。
4. 创建调度管理器。
5. 读取调度持久化状态。
6. 如果 `enabled=true`，启动周期调度。
7. 如果 `enabled=true` 且 `run_on_start=true`，启动后立即执行一次账户快照。
8. 启动 HTTP 服务。

周期任务当前只做账户快照：

- 读取手动持仓台账。
- 读取手动资金账户。
- 通过 market provider 获取行情。
- 生成账户估值快照。
- 保存快照到 SQLite。
- 写入 JSONL 或结构化日志。
- 更新调度最近运行状态。

任务异常不得导致 HTTP 服务退出。异常应记录到调度状态，并在 `GET /service/status` 中可见。

## 存储设计

### API 认证状态

新增表建议：

```sql
CREATE TABLE api_auth_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  password_hash TEXT,
  token_secret TEXT,
  updated_at TEXT NOT NULL
);
```

实现阶段也可以拆成更明确的 `api_password` 和 `api_token_secret` 存储，但必须满足：

- 不保存明文访问密码。
- 不保存明文 token。
- `token_secret` 是本机签名密钥，不是用户密码或已签发 token；不得写入日志、错误响应或 git。
- 支持判断是否 configured。
- 支持 setup required 状态下落库访问密码哈希。

### 调度状态

新增表建议：

```sql
CREATE TABLE scheduler_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
  interval_seconds INTEGER NOT NULL CHECK (interval_seconds >= 1),
  run_on_start INTEGER NOT NULL CHECK (run_on_start IN (0, 1)),
  last_started_at TEXT,
  last_finished_at TEXT,
  last_status TEXT,
  last_reason TEXT,
  last_error TEXT,
  last_snapshot_id INTEGER,
  updated_at TEXT NOT NULL
);
```

`enabled` 是期望状态，必须跨进程重启保留。`last_status` 可使用 `success`、`failed`、`running` 等简洁枚举。

### 账户快照

复用现有 `account_snapshots` 表保存快照 payload，并补充 repository 方法：

- 保存新快照。
- 根据 ID 读取快照。
- 读取最新快照。

快照 payload 不得包含外部第三方原始响应或敏感凭据。

## 配置

新增或确认以下配置：

- `QT_API_HOST`，默认 `127.0.0.1`。
- `QT_API_PORT`，默认 `8000`。
- `QT_API_ACCESS_PASSWORD`，可选启动密码，不建议长期只放环境变量。
- `QT_API_TOKEN_TTL_SECONDS`。
- `QT_API_TOKEN_SECRET`，可选；未配置时生成本地随机密钥并落库。
- `QT_SERVICE_RUN_ON_START_WHEN_SCHEDULER_ENABLED`，默认 `true`。
- `QT_DATABASE_PATH`。
- `QT_LOG_DIR`。
- `QT_MARKET_PROVIDER`。
- `QT_INTRADAY_INTERVAL_SECONDS`。
- `QT_TIMEZONE`。
- `QT_ENABLE_MARKET_FETCH`。

`.env.example` 只能放占位符，不得包含真实密码、token 或 API key。

## 安全边界

- HTTP API 不提供自动真实下单能力。
- HTTP API 不模拟点击交易客户端。
- HTTP API 不读取真实券商账号、密码、cookie、token 或 API key。
- 持仓写接口只维护手动持仓台账。
- 资金写接口只维护手动资金账户。
- 行情数据只通过 adapter 获取，不作为真实持仓或真实资金来源。
- 未设置访问密码时服务可启动，但必须提示 setup required，并默认不开放业务接口。
- 日志和错误响应不得输出访问密码、token、密码哈希或签名密钥。

## 测试要求

实现阶段应覆盖：

- 服务未设置访问密码时可以启动。
- 未设置访问密码时 `GET /service/status` 返回 setup required。
- setup 接口可以将访问密码哈希落库。
- 已设置密码后 setup 重复调用失败。
- 登录成功、登录失败、无 token、非法 token、过期 token。
- 所有受保护业务接口在未认证时返回统一 `unauthorized`。
- Pydantic 校验错误转换为统一 `validation_error`。
- 持仓 API 的列表、详情、新增、更新、删除、JSON 批量导入、CSV 导入和 CSV 导出。
- 资金 API 的初始化、重复初始化失败、转入、转出超额失败、现金校准必须有 note、流水查询。
- 账户快照 API 的无快照、生成新快照、读取 latest、partial/unavailable 状态保留。
- 调度 API 的状态查询、start 持久化、stop 持久化、run-once 记录最近结果。
- 服务启动时读取 `enabled=true` 并恢复调度。
- `run_on_start=true` 时恢复后立即执行一次任务。
- CLI 现有测试继续通过，确保 API 没有引入第二套业务逻辑。

测试应优先使用 fake market provider 和临时 SQLite 数据库，不让真实网络成为核心单元测试依赖。

## 文档影响

实现本设计时需要同步更新：

- `docs/project-spec.md`：补充 HTTP API 纳入后台服务入口，仍不涉及前端实现。
- `docs/data-sources.md`：补充 API 写操作仍以手动台账和手动资金账户为权威源。
- `docs/api.md`：新增认证、错误格式、接口清单、调度行为和安全边界。
- `.env.example`：新增 API 密码、token TTL、监听地址端口、调度恢复配置占位符。

本轮不修改 `docs/recommendation-contract.md` 的建议契约。人工反馈 API 暂不开放，待推荐模块和审计链路落地后再设计。

## 实现默认决策

- `logout` 首版不实现 token 撤销表，只返回成功并由前端清除 token；token 依靠短 TTL 失效。
- 默认监听 `127.0.0.1`。如果用户显式绑定 `0.0.0.0` 且处于 setup required 状态，服务允许启动但必须输出高风险警告，并且业务接口仍不可用。
- `QT_API_TOKEN_SECRET` 未配置时，服务生成本地随机签名密钥并落库到 SQLite；该密钥不得出现在日志、错误响应或 `.env.example` 中。
- setup 完成前，除健康检查、服务状态和 setup 接口外，其余业务接口都返回 `auth_setup_required` 或 `unauthorized`。
