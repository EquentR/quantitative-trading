# 行情刷新、质量判定与重复建议修复实施计划

> 实施必须逐任务执行 TDD RED/GREEN，并在每个任务后由独立后台 agent 验收。规格来源：`docs/superpowers/specs/2026-07-17-market-refresh-quality-and-dedup-fix-design.md`。

**目标：** 修复人工行情刷新、无计划 history 引用、报价质量误判、分钟缓存异常路径、非交易时段展示刷新、并发运行误报、重复 HOLD 展示和通知洪水。

**架构：** 继续使用 `DecisionWorkflow`、现有 backfill/intraday capture run 和统一 repository。新增显式 execution mode、日 K cutoff resolver、不可变 history materializer、通知 canonical/link 投影和前端两阶段 coordinator；不新增第三方直连路径或交易能力。

**安全约束：** `display_only` 在账户估值、策略、风控、建议和通知之前硬退出；活动计划冻结 history 优先；stale/failed quote 和 degraded/stale strength 不触发交易动作；不删除历史建议/通知/审计。

**TDD 执行规则：** 每个下列 Cycle 必须独立执行：先只写该行为测试并运行到预期失败（RED），再写满足该测试的最小实现并运行到通过（GREEN），最后运行该 Cycle 列出的邻近回归。禁止把同一任务的全部 RED 堆完后一次实现。每个 Cycle 的 RED/GREEN 命令与关键输出写入验收记录。

## Task 1：存储与领域模型扩展

**修改：**

- `src/quantitative_trading/storage/sqlite.py`
- `src/quantitative_trading/market/models.py`
- `src/quantitative_trading/market/repositories.py`
- `src/quantitative_trading/market/schema.py`
- `src/quantitative_trading/recommendation/models.py`
- `src/quantitative_trading/notification/repository.py`
- `src/quantitative_trading/api/routes/market_read.py`
- `tests/test_sqlite_storage.py`
- `tests/test_market_heavy_models.py`
- `tests/test_market_heavy_repository.py`
- `tests/test_notification_service.py`
- `tests/test_api_market_read.py`

**步骤：**

- [x] Cycle 1A：RED/GREEN `MarketCaptureRun`/`MarketInputSnapshot` 保存 mode、dates、requested scope、lease；旧 JSON/DB 行有保守默认。运行 `pytest -q tests/test_market_heavy_models.py tests/test_market_heavy_repository.py`。
- [x] Cycle 1B：RED/GREEN `HistorySnapshot` 保存版本化 completeness/coverage evidence，旧 payload 默认为 unverifiable，repository round-trip 保留。运行同一组 heavy model/repository 测试。
- [x] Cycle 1C：RED/GREEN migration 创建 `notification_canonical_groups(canonical_key PK, notification_id UNIQUE FK RESTRICT)` 和 `recommendation_notification_links(recommendation_id PK, notification_id/canonical_key FK RESTRICT)` 及 notification ID 索引；增加 `condition_fingerprint_version` 兼容字段。运行 `pytest -q tests/test_sqlite_storage.py tests/test_notification_service.py`。
- [x] Cycle 1D：RED/GREEN 旧数据库升级、migration 注入失败回滚、服务重启后 run list/detail 可读取旧 run/input/history，枚举和 API status 投影兼容。运行 `pytest -q tests/test_sqlite_storage.py tests/test_api_market_read.py`。
- [x] VERIFY：运行上述测试文件和 `git diff --check`。
- [x] REVIEW：后台 agent 检查 migration 兼容、系统告警不受影响、无敏感数据字段。

## Task 2：通知 canonical 去重、迁移和 current/history 查询

**修改：**

- `src/quantitative_trading/notification/dispatcher.py`
- `src/quantitative_trading/notification/repository.py`
- `src/quantitative_trading/notification/service.py`
- `src/quantitative_trading/recommendation/identity.py`
- `src/quantitative_trading/recommendation/models.py`
- `src/quantitative_trading/storage/sqlite.py`
- `src/quantitative_trading/api/routes/notifications.py`
- `src/quantitative_trading/api/routes/market_read.py`
- `src/quantitative_trading/api/routes/feedback.py`
- `src/quantitative_trading/feedback/service.py`
- `src/quantitative_trading/cli.py`
- `docs/recommendation-contract.md`
- `docs/api.md`
- `tests/test_notification_dispatcher.py`
- `tests/test_notification_service.py`
- `tests/test_api_notifications_audit.py`
- `tests/test_api_market_read.py`
- `tests/test_api_feedback.py`
- `tests/test_cli.py`

**步骤：**

- [ ] Cycle 2A：RED/GREEN fingerprint v2 排除纯时间元数据，canonical key 使用上海交易日、symbol、action、plan_id、plan_version、v2 fingerprint；实质变化/跨日新建。运行 identity/dispatcher 定向测试。
- [ ] Cycle 2B：RED/GREEN 跨周期相同条件保留两条建议，只创建一个 notification，并在单事务按 notification/audit -> canonical group -> link 写入；并发冲突回滚后读取已提交 group，再幂等补 link。运行 dispatcher/service 测试。
- [ ] Cycle 2C：RED/GREEN legacy migration 从 payload 重算 v2 且不改旧 recommendation identity；NULL/损坏 payload/缺失 recommendation 各自独立成组并脱敏告警。混合状态按 `feedback_recorded > read > unread`、同级最新选择 canonical。覆盖幂等、唯一冲突和完整回滚。
- [ ] Cycle 2D：RED/GREEN `view=current|history`；无参数保持 history。current/unread 是 canonical recommendation UNION system_alert，行情扫描器逐标计数和 CLI unread 使用同一 service helper。
- [ ] Cycle 2E：RED/GREEN 改造 route 中按原 recommendation ID 扫描通知的 helper；最新周期 recommendation 通过 link 更新 canonical 旧 notification，不改变其原始 recommendation/audit。legacy 无 link 时只保守匹配完全相同的原 recommendation ID，不猜 canonical；重复 dispatch 不重置处理状态。
- [ ] 同步 recommendation/API 文档并运行 `pytest -q tests/test_docs_examples.py`。
- [ ] VERIFY：运行 notification/feedback 定向测试。
- [ ] REVIEW：后台 agent 核对系统告警闭环、read/feedback 状态不重置、plan ID/version 边界。

## Task 3：建议 current/history 投影

**修改：**

- `src/quantitative_trading/recommendation/repository.py`
- `src/quantitative_trading/api/routes/recommendations.py`
- `src/quantitative_trading/notification/repository.py`
- `docs/recommendation-contract.md`
- `docs/api.md`
- `tests/test_recommendation_repository.py`
- `tests/test_api_recommendations.py`

**步骤：**

- [ ] Cycle 3A：RED/GREEN current 用 SQL 每 symbol 稳定选最新，history 保留全部且两者 total/pagination 正确；API 无 view 参数保持旧 history 数据和 Recommendation item 结构。
- [ ] Cycle 3B：RED/GREEN 显式 `view=current|history` 返回 `RecommendationListItem`，投影不可变 recommendation 加可空 notification ID/status；legacy 无 view 和详情/trace 不变。
- [ ] 同步 recommendation/API 文档并运行 docs tests。
- [ ] VERIFY：运行 recommendation repository/API 测试。
- [ ] REVIEW：后台 agent 核对大数据分页不在前端分组、详情/trace 契约不变。

## Task 4：安全日 K cutoff 与本地 History 固化

**修改：**

- `src/quantitative_trading/market/calendar.py`
- `src/quantitative_trading/market/models.py`
- `src/quantitative_trading/market/repositories.py`
- `src/quantitative_trading/market/adapters.py`
- `src/quantitative_trading/market/backfill.py`
- `src/quantitative_trading/market/cli_service.py`
- `src/quantitative_trading/decision/workflow.py`
- `src/quantitative_trading/api/routes/service_workflows.py`
- `docs/data-sources.md`
- `docs/project-spec.md`
- `tests/test_market_calendar.py`
- `tests/test_market_backfill.py`
- `tests/test_market_heavy_models.py`
- `tests/test_market_heavy_repository.py`
- `tests/test_market_heavy_adapters.py`
- `tests/test_decision_close_workflow.py`
- `tests/test_api_workflows.py`

**步骤：**

- [x] Cycle 4A：RED/GREEN 10:00、午休、盘后未就绪/已就绪、开盘前、周末解析 effective trade date/history cutoff；运行 calendar/API 定向测试。
- [x] Cycle 4B：RED/GREEN `as_of_mode=latest_complete` 与 trade_date 互斥，只接受启用股票池子集；首次 scope 做 5 日 correction，同 scope 复用。
- [x] Cycle 4C：RED/GREEN adapter 的 `DailyBarCoverageEvidence` protocol/模型/映射；不支持证据的旧 provider 保守 unverifiable，不能从短响应猜 listing date。
- [x] Cycle 4D：RED/GREEN 新上市短历史凭权威 listing date 或完整请求窗口 evidence 判定；覆盖合法停牌窗口无 bar，不按每个 XSHG 日强求一根。统一使用 `MIN_HISTORY_ROWS=20` 常量。
- [x] Cycle 4E：RED/GREEN 无活动计划时从版本化 facts 固化不可变 history 并挂 MarketInput；活动计划冻结 history 优先，content digest/member version/evidence round-trip。
- [x] Cycle 4F：RED/GREEN intraday 或已完成 scope 本地固化不请求 provider；真实缺口才补，CLI/close correction 不回归。
- [x] 同步 project/data-source 文档并运行 docs tests。
- [x] VERIFY：运行 calendar/backfill/workflow/API 定向测试。
- [x] REVIEW：后台 agent 核对未收盘日 K、前复权、content digest、计划冻结引用和幂等陷阱。

## Task 5：分钟缓存异常兜底与 dataset-specific quality

**修改：**

- `src/quantitative_trading/decision/models.py`
- `src/quantitative_trading/decision/workflow.py`
- `src/quantitative_trading/decision/service.py`
- `src/quantitative_trading/strategy/service.py`
- `docs/trading-policy.md`
- `docs/recommendation-contract.md`
- `tests/test_decision_close_workflow.py`
- `tests/test_decision_engine.py`
- `tests/test_strategy_service.py`

**步骤：**

- [x] Cycle 5A：RED/GREEN provider 获取异常 + 同 symbol/同日缓存计算 strength，至少 degraded；lag 超阈值 stale；received/written=0、actual rows/source/warning 正确。
- [x] Cycle 5B：RED/GREEN 无同日缓存 failed，跨日/跨 symbol 不复用；缓存校验/计算异常不被 provider catch 吞掉。
- [x] Cycle 5C：逐一 RED/GREEN quote usable/history missing、stale quote/history good、failed quote/history good、两者可用四象限，并验证独立 machine reason。
- [x] Cycle 5D：RED/GREEN cached degraded/stale strength 不能确认 buy/add；只保留当前已实现且不依赖 history 的硬风险，不新增机械成本止损。`512480` quote usable/history missing/available_quantity=0 保持 HOLD，理由不得是 quote unavailable。
- [x] 同步 trading/recommendation 文档并运行 docs tests。
- [x] VERIFY：运行 workflow/decision/strategy 定向测试。
- [x] REVIEW：后台 agent 核对不跨日、不提升 quote、overall 不再错误阻断所有价格动作。

## Task 6：显式 display-only 工作流与 API/CLI

**修改：**

- `src/quantitative_trading/decision/workflow.py`
- `src/quantitative_trading/api/routes/service_workflows.py`
- `src/quantitative_trading/cli.py`
- `src/quantitative_trading/runtime/service_runner.py`
- `docs/project-spec.md`
- `docs/trading-policy.md`
- `docs/api.md`
- `tests/test_decision_close_workflow.py`
- `tests/test_api_workflows.py`
- `tests/test_cli.py`
- `tests/test_runtime_decision_workflow.py`

**步骤：**

- [ ] Cycle 6A：RED/GREEN display-only run ID/key 使用 mode + effective date + requested_at 上海墙钟 3 分钟 bucket；decision 保持交易周期。mode/dates/scope/10 分钟 `lease_expires_at` 进入 run、MarketInput、响应和审计。
- [ ] Cycle 6B：RED/GREEN 在估值/strategy/risk/recommendation/notification dispatcher 前硬门禁；除表行数不变外，用 spies 断言这些依赖根本未调用，recommendation_ids 恒空。
- [ ] Cycle 6C：RED/GREEN 普通非交易 intraday 仍 422，交易时段和 scheduler 永远 decision，周末 display-only 最近完成会话；CLI 旧 force/reason 语义不变。
- [ ] Cycle 6D：RED/GREEN 旧数据库字段迁移后服务重启可恢复/读取；display-only bucket 不永久复用旧 run。
- [ ] Cycle 6E：RED/GREEN 盘后无源时间 quote 只有严格匹配同日 K 收盘才获得验证市场时间。
- [ ] 同步 project/trading/API 文档并运行 docs tests。
- [ ] VERIFY：运行 workflow/API/CLI/runtime 定向测试。
- [ ] REVIEW：后台 agent 重点检查 display-only 无任何决策副作用和审计可追溯。

## Task 7：前端两阶段刷新与运行跟随

**修改：**

- `src/web/src/api/types.ts`
- `src/web/src/composables/useMarketRefreshCoordinator.ts`（新增）
- `src/web/src/queries/service.ts`
- `src/web/src/queries/market.ts`
- `src/web/src/queries/recommendations.ts`
- `src/web/src/queries/notifications.ts`
- `src/web/src/features/market/MarketPage.vue`
- `src/web/src/features/monitoring/MonitoringPage.vue`
- `src/web/src/features/recommendations/RecommendationListPage.vue`
- `src/web/src/features/recommendations/RecommendationDetailDrawer.vue`
- `src/web/src/features/review/ReviewPage.vue`
- `src/web/src/features/dashboard/DashboardPage.vue`
- `src/web/src/mocks/handlers.ts`
- `src/web/tests/market-workbench.test.ts`
- `src/web/tests/monitoring.test.ts`
- `src/web/tests/recommendations.test.ts`
- `src/web/tests/notifications.test.ts`
- `src/web/tests/query-hooks.test.ts`
- `src/web/tests/api-types.test.ts`

**步骤：**

- [ ] Cycle 7A：RED/GREEN coordinator 顺序执行 backfill/intraday，分别保存 run/status/warning/reused。Stage 1 业务 degraded/failed 继续 Stage 2；auth/validation/DB/无可信 run 的 transport fatal 才停。覆盖 Stage 1 失败而 Stage 2 成功的反向 partial。
- [ ] Cycle 7B：RED/GREEN 409 精确轮询，短暂 404、卸载取消、provider disabled 和服务重启。等待上限取后端 lease_expires_at/retry_after；归一 response `success` 与 detail `succeeded`，未知状态报契约错误。
- [ ] Cycle 7C：RED/GREEN 409 run scope 校验；不同 backfill scope 完成后只对 missing symbols 重提一次，仍不足则 partial，不无限重试。intraday 校验统一股票池覆盖。
- [ ] Cycle 7D：RED/GREEN terminal 后 invalidate 全部 query；degraded 显示部分可用，display-only 显示未生成建议，真实 failed 才显示失败。
- [ ] Cycle 7E：RED/GREEN 行情页三阶段按钮、监控/建议页共享错误处理；建议页显式 `view=current` 和 current/history 控件，详情使用 DTO link。Dashboard 请求 current，Review 可切 current/history，无参数 API 兼容测试保持 history。
- [ ] VERIFY：使用 Node 24 运行前端定向 Vitest 与 build。
- [ ] REVIEW：后台 agent 检查错误码处理、轮询泄漏、所有通知/建议消费者、移动/桌面文本与布局。

## Task 8：规约同步、全量回归和真实服务验收

**修改：**

- `docs/project-spec.md`
- `docs/trading-policy.md`
- `docs/data-sources.md`
- `docs/recommendation-contract.md`
- `docs/api.md`
- `README.md`
- `src/web/e2e/market-refresh.spec.ts`（新增）

**步骤：**

- [ ] 复核各任务已同步 display-only、cutoff/history、dataset-specific quality、分钟缓存、current/history 和通知 link 契约；这里只补遗漏，不延后语义文档。
- [ ] 运行 docs 契约测试、完整 Python 测试、Node 24 全部 Vitest、生产构建和 Playwright 桌面/移动目标流程。
- [ ] `market-refresh.spec.ts` 使用临时数据库和确定性 provider，覆盖桌面/移动、409 跟随、display-only 零决策副作用、current/history 与部分成功；不得污染现场数据库。
- [ ] 使用临时数据库验证周末/非交易 display-only；真实服务只做安全的行情冒烟检查。
- [ ] 检查 `git diff --check`、敏感信息、提交身份和工作树。
- [ ] 后台 agent 对规格逐条验收，并独立复跑关键测试。
- [ ] 使用 `Equent <ryq2836@qq.com>` 提交实现；不得修改系统默认 git config。
