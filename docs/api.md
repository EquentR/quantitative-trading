# 后台 HTTP API

## 安全边界

HTTP API 是后台服务接口，只维护手动持仓台账、手动资金账户、本地账户快照和本地调度状态。它不自动真实下单，不模拟点击或控制真实交易客户端，不读取、保存或提交真实券商账号、密码、cookie、token 或 API key。

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

## 服务与调度接口

```text
GET /api/v1/service/status
POST /api/v1/service/scheduler/start
POST /api/v1/service/scheduler/stop
POST /api/v1/service/run-once
```

调度 start/stop 只切换本地调度状态和账户快照任务，不触发真实下单，不控制真实交易客户端，也不修改真实券商账户。`run-once` 只执行一次本地账户快照作业。

## CLI 关系

`qt service run` 启动统一后台服务，组合 HTTP API 和调度器。`qt service debug-run --once` 仍是本地调试快照辅助入口，用于手动执行一次快照检查。

HTTP API、CLI 和后台调度器共享相同的 service、repository、adapter、风控和审计日志逻辑，不为不同入口维护独立口径。
