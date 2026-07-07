# 前端实现设计

## 背景

本项目是个人 A 股短线量化决策辅助系统。系统只输出可解释建议，不自动真实下单，不控制真实交易客户端，不读取真实交易凭据，也不承诺确定性收益。

后台 HTTP API 已在 `docs/superpowers/specs/2026-07-07-http-api-design.md` 中完成设计，并计划覆盖认证、服务状态、调度控制、手动持仓台账、手动资金账户和账户快照。前端设计基于该 API 范围展开，作为后续实现前的需求与架构基线。

当前 `docs/project-spec.md` 将 Web UI 列为首版非目标。本设计因此定位为后续阶段的本地控制台与决策工作台骨架，不改变首版后端和 CLI 的安全边界。

## 目标

- 使用 Vue 3、Vite、TypeScript、Tailwind CSS 和 shadcn-vue 风格组件构建前端。
- 第一阶段完整覆盖后台 HTTP API 已设计能力：认证与 setup、服务状态、调度控制、手动持仓台账读写、手动资金账户读写、账户快照读取与生成。
- 将前端定位为“本地量化控制台 + 决策工作台骨架”。
- 主导航按未来交易工作流组织：今日仪表盘、准备、监控、复盘。
- 首页仪表盘融合总览和资源管理摘要：服务健康、调度状态、账户快照、持仓摘要、资金摘要和待处理风险。
- 所有写操作都明确表达为维护手动台账或手动资金账户，不暗示真实交易已经发生。
- 为后续推荐、人工反馈、审计日志和模拟组合对比预留信息架构入口。

## 非目标

- 不实现自动真实下单。
- 不模拟点击或控制真实交易客户端。
- 不读取、保存或提交真实券商账号、密码、cookie、token 或 API key。
- 不将持仓或资金写操作描述为真实交易行为。
- 不在推荐 API 尚未落地时伪造交易建议、人工反馈、审计日志或模拟组合数据。
- 不实现多用户、租户、角色权限或公网部署控制台。
- 不替代 CLI 和后台服务的核心业务逻辑。

## 技术栈决策

采用以下技术栈：

- Vue 3 + Vite + TypeScript。
- Tailwind CSS 作为主要样式系统。
- shadcn-vue / Radix Vue 风格组件作为基础 UI 组件来源。
- TanStack Query for Vue 管理 API 请求、缓存、刷新、加载态和错误态。
- Pinia 只保存会话级状态，例如 API base URL、access token 和本地显示偏好。
- zod + vee-validate 管理表单校验。
- Vue Router 管理页面路由。
- MSW 或同类 mock server 用于前端测试中的 `/api/v1` 响应模拟。

选择该方案的原因：

- Tailwind 与 shadcn-vue 契合度高，适合构建克制、密集、工具型的本地控制台。
- TanStack Query 能稳定处理后端先行、API 渐进落地时的 loading、error、refetch 和缓存失效。
- Pinia 不承载资源缓存，避免请求状态散落在业务 store 中。
- Nuxt 3 暂不采用，因为本地控制台不需要 SSR，额外约定会抬高复杂度。

## 信息架构

应用采用左侧主导航和顶部状态栏。

主导航包括：

- 今日仪表盘。
- 准备。
- 监控。
- 复盘。
- 设置。

顶部状态栏展示：

- API 连接状态。
- 认证状态。
- 调度运行状态。
- 最近数据时间。
- 最新账户快照状态。

当出现 `setup_required`、`unauthorized`、`market_data_unavailable`、`snapshot_not_found` 或账户快照 `partial` / `unavailable` 时，顶部或页面级 alert 必须显式提示，不得将缺失数据展示为正常估值。

## 页面设计

### 今日仪表盘

登录后的默认页。目标是让用户每天打开后先判断系统是否可用、数据是否可信、是否需要手动处理。

展示内容：

- 认证状态和 API 服务状态。
- 调度期望状态、实际运行状态、间隔、时区、下次运行时间。
- 最近运行开始时间、结束时间、状态、原因和错误摘要。
- 最近账户快照 ID、快照状态、数据时间和 warnings。
- 账户估值摘要：现金余额、持仓市值、总资产、浮动盈亏、总盈亏、仓位比例。
- 持仓摘要：持仓数量、可用数量异常提示、最近台账更新时间。
- 资金摘要：现金余额、净本金、最近资金流水。
- 待处理事项：未初始化资金账户、无快照、行情不可用、调度失败、认证 setup required。

仪表盘可以提供快捷入口：

- 生成一次账户快照。
- 进入持仓台账维护。
- 进入资金账户维护。
- 启动或停止调度。

### 准备

交易日前的数据维护区，承载完整读写能力。

子功能：

- 持仓台账列表、详情、新增、编辑、删除。
- 持仓 JSON 批量导入、CSV 导入和 CSV 导出。
- 资金账户初始化。
- 记录模拟银证转入、模拟银证转出。
- 现金校准。
- 资金流水列表。

文案规则：

- 持仓写操作使用“保存台账”“删除台账记录”“导入台账”。
- 删除确认必须说明“删除台账记录不代表真实卖出或撤单”。
- 资金转入/转出使用“记录模拟银证转入/转出”。
- 现金校准必须要求备注，并提示“用于修正手动资金账户，不代表券商资金变化”。

### 监控

服务运行与账户快照区。

子功能：

- 查看服务状态。
- 启动调度。
- 停止调度。
- 手动运行一次账户快照任务。
- 查看最新持久化快照。
- 生成并保存新快照。
- 展示快照 `status`、`warnings`、最近错误和数据缺口。

调度文案必须表达为后台账户快照任务控制，不描述为交易监控、自动下单或自动操作。

### 复盘

第一阶段作为后续能力预留模块。

允许展示：

- 空状态。
- 后续会接入的能力说明。
- 推荐记录、人工执行反馈、审计日志、模拟组合对比的入口占位。

不得展示：

- 假交易建议。
- 假人工反馈。
- 假收益表现。
- 任何暗示系统已能生成或执行真实交易的内容。

### 设置

本地控制台设置区。

包含：

- API base URL。
- 当前 token 状态。
- 退出登录。
- 本地显示偏好，例如金额小数位、时间显示、表格密度。

不包含：

- 明文访问密码保存。
- 真实券商凭据配置。
- 外部 API key 管理。

## 前端模块边界

建议目录结构：

```text
frontend/
  src/
    api/
    app/
    components/
      ui/
      domain/
    features/
      auth/
      dashboard/
      preparation/
      monitoring/
      review/
      settings/
    queries/
    router/
    stores/
    types/
    utils/
```

模块职责：

- `api/`：HTTP client、token 注入、统一错误解析、文件上传下载。
- `queries/`：TanStack Query hooks，例如 `useServiceStatusQuery`、`usePositionsQuery`、`useCashAccountQuery`。
- `stores/`：Pinia 会话状态，例如 token、API base URL、显示偏好。
- `features/auth`：setup-password、login、logout、me。
- `features/dashboard`：仪表盘聚合展示。
- `features/preparation`：持仓、资金账户、资金流水、导入导出。
- `features/monitoring`：服务调度、run-once、账户快照。
- `features/review`：复盘占位与后续接口边界。
- `components/ui`：shadcn-vue 基础组件。
- `components/domain`：状态徽标、风险提示、金额/比例/时间展示、安全文案组件。

## API 对接与状态管理

前端启动流程：

1. 读取本地 API base URL 和 token。
2. 调用 `GET /api/v1/service/status`。
3. 如果 `auth_status=setup_required`，进入 setup 页面。
4. 如果已配置但无 token，进入登录页。
5. 登录成功后保存 token，并进入今日仪表盘。
6. 业务页面通过 TanStack Query 拉取数据。

写操作成功后的缓存失效规则：

- 持仓新增、编辑、删除、导入后，刷新 positions、dashboard、account snapshot 相关 query。
- 资金初始化、转入、转出、现金校准后，刷新 cash account、cash transactions、dashboard、account snapshot 相关 query。
- 生成快照或 run-once 后，刷新 account snapshot、latest snapshot、service status、dashboard。
- 调度 start/stop 后，刷新 service status 和 dashboard。

错误解析：

后端统一错误格式为：

```json
{
  "error": {
    "code": "validation_error",
    "message": "validation failed",
    "details": {}
  }
}
```

前端必须保留 `code` 和 `details`，用于表单字段错误、全局 alert 和可展开调试详情。

## 认证与 Setup 流程

setup required 状态：

- 只展示设置访问密码页面。
- 文案说明“密码用于保护本地 HTTP API，不会保存明文”。
- setup 成功后进入登录或自动获取 token，具体实现可根据后端行为决定。

登录状态：

- 登录页只要求访问密码。
- 业务接口使用 `Authorization: Bearer <access_token>`。
- token 过期、非法或缺失时回到登录页，并保留目标路由。
- logout 首版清除前端 token；如果后端不实现 token 撤销，界面不得暗示服务端 token 已被强制失效。

## 写操作安全文案

全局原则：

- 使用“记录”“维护”“保存到手动台账”“生成快照”等表达。
- 避免“下单”“委托”“成交”“买入成功”“卖出成功”“提现成功”等真实交易语义。
- 对删除和资金校准等高风险操作使用确认弹窗。
- 表单提交成功 toast 必须表达为本地系统状态变化，不表达为真实账户变化。

示例：

- 持仓新增成功：`已保存到手动持仓台账`。
- 持仓删除成功：`已删除手动台账记录，不代表真实卖出`。
- 资金转入成功：`已记录模拟银证转入`。
- 资金校准成功：`已完成手动资金账户现金校准`。
- 快照生成成功：`已生成并保存账户快照`。

## 错误处理

常见错误处理要求：

- `auth_setup_required`：进入 setup 引导。
- `unauthorized`：进入登录页。
- `validation_error`：字段级展示，details 可展开。
- `cash_account_not_initialized`：提示初始化资金账户。
- `snapshot_not_found`：提示生成账户快照。
- `market_data_unavailable`：提示行情不可用，禁止将估值展示为完整可用。
- `scheduler_error`：在监控页和仪表盘展示最近错误摘要。
- `internal_error`：展示通用错误，details 默认折叠。

当账户快照状态为 partial 或 unavailable 时：

- 账户级总市值、总资产、仓位比例不得被正常高亮展示。
- 可展示字段必须带有数据质量标记。
- warnings 应在仪表盘和快照详情中可见。

## 组件设计

基础组件来自 shadcn-vue 风格体系：

- Button、Input、Textarea、Select。
- Dialog、AlertDialog、Sheet。
- Tabs、Badge、Alert、Toast。
- Table、DropdownMenu、Tooltip。
- Form、Checkbox、Switch。

领域组件：

- `AuthStatusBadge`：configured、setup_required、unknown。
- `SchedulerStatusBadge`：enabled/running/stopped/error。
- `SnapshotStatusBadge`：complete、partial、unavailable。
- `ApiErrorAlert`：统一错误展示。
- `DataTime`：展示带时区 ISO 时间。
- `MoneyValue`：金额格式化，支持 unavailable。
- `RatioValue`：比例格式化，支持 unavailable。
- `SafetyCopy`：统一安全边界提示文案。
- `ConfirmManualLedgerDeleteDialog`：删除持仓台账记录确认。
- `CashOperationForm`：资金初始化、模拟转入、模拟转出、现金校准共用表单基础。

## 测试策略

单元测试：

- API client token 注入和错误解析。
- 金额、比例、时间格式化。
- 表单 zod schema。
- 状态 badge 映射。
- 安全文案 helper。

组件测试：

- setup 页面。
- 登录页面。
- 持仓新增、编辑、删除表单。
- 持仓导入导出控件。
- 资金初始化、转入、转出、校准表单。
- 调度控制组件。
- 快照状态和 warnings 展示。

页面级测试：

- setup required -> setup -> login -> dashboard。
- 登录后查看仪表盘。
- 持仓 CRUD 后刷新列表和仪表盘。
- 资金初始化与流水刷新。
- 调度 start、stop、run-once。
- snapshot_not_found、partial、unavailable 分支。
- token 过期后回登录页并保留目标路由。

测试数据策略：

- 使用 MSW 或同类 mock server 模拟 `/api/v1`。
- 不依赖真实后端服务。
- 不依赖真实 AkShare 网络请求。
- 不使用真实密码、token、cookie、API key。

视觉验证：

- 桌面宽屏。
- 窄屏或小窗口。
- 表格、抽屉、弹窗、顶部状态栏不得重叠。
- 长股票名称、长错误消息、长 warnings 不得撑破布局。

## 后续阶段预留

后续后端能力落地后，前端可扩展：

- 推荐列表和推荐详情。
- 人工执行反馈。
- 审计日志查询。
- 收盘计划和盘中触发记录。
- 模拟组合操作结果对比。
- 复盘视图和策略表现分析。

这些能力必须继续遵守项目安全边界：前端不自动真实下单，不控制真实交易客户端，不把模拟组合结果当作真实持仓来源，不把建议描述为确定性收益。

## 设计确认

已确认的关键决策：

- 第一阶段采用完整本地读写控制台，而不是只读界面。
- 主模块以交易流程为主：今日仪表盘、准备、监控、复盘。
- 今日仪表盘融合总览优先和资源管理摘要。
- UI 技术栈采用 Vue 3 + Vite + TypeScript + Tailwind CSS + shadcn-vue。
- API 状态管理采用 TanStack Query for Vue。
