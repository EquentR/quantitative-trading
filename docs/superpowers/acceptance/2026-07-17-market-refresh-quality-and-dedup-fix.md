# 行情刷新、质量判定与重复建议修复验收记录

对应实施计划：
`docs/superpowers/plans/2026-07-17-market-refresh-quality-and-dedup-fix.md`。

## 证据边界

本记录创建于 2026-07-18，晚于 Task 1-8 的主要实现提交。此前仓库没有逐 Cycle
验收文件，因此下列事实必须分开理解：

- Git 能证明测试和实现最终进入了哪些提交，但不能单独证明测试一定先于实现运行。
- Task 1-8 各 Cycle 的历史 RED 原始命令输出没有留存，无法诚实重建；表中明确标为
  `未留存`，不使用当前 GREEN 输出倒推历史 RED。
- 当前 GREEN、构建和 E2E 结果来自实际重跑；可重复命令和关键输出列在本文。
- 原始逐 Task reviewer 对话不是仓库 artifact，无法从 Git 重建。本文只记录当前可核验的
  completion audit 和补充修复 reviewer 结论。

## 提交边界

| 范围 | 提交 | 说明 |
| --- | --- | --- |
| Task 1 foundations | `d131549` | market execution、history evidence、notification link schema |
| Task 2/3 与 Task 4 起始 | `5320237` | canonical/link/current-history、calendar/backfill 基础 |
| Task 4 short history | `a98a49b` | listing metadata 完整性证据 |
| Task 4 frozen/local history | `6cfdc9e` | 不可变 history 与计划冻结引用 |
| Task 4 correction/reuse | `3420944` | correction、local-first 与复用边界 |
| Task 5 | `e1c5472` | dataset-specific quality 与分钟缓存 provenance |
| Task 6 | `4c494c0` | display-only 隔离、API/CLI 与审计 |
| Task 7 | `1c2beea` | Web 两阶段 coordinator、409 跟随与 current/history 消费 |
| Task 8 | `e6e5b31` | docs、确定性 E2E、真实服务只读冒烟与收尾修复 |
| completion audit 修复 | `a419c62` | 两阶段 live run/status/reused/warnings 三页展示 |
| completion audit 覆盖 | `820e2db` | plan ID/version canonical 边界回归 |

以上提交的 author 均为 `Equent <ryq2836@qq.com>`。

## Cycle 记录

`RED` 列的“未留存”是证据缺口声明，不代表 Cycle 没有执行，也不声称执行过。
`GREEN 重放` 是当前代码的可重复验证入口；最终全量套件还会覆盖所有行。

| Cycle | 历史 RED | GREEN 重放命令 | 主要提交 |
| --- | --- | --- | --- |
| 1A | 未留存 | `uv run pytest -q tests/test_market_heavy_models.py tests/test_market_heavy_repository.py` | `d131549` |
| 1B | 未留存 | 同 1A | `d131549` |
| 1C | 未留存 | `uv run pytest -q tests/test_sqlite_storage.py tests/test_notification_service.py` | `d131549` |
| 1D | 未留存 | `uv run pytest -q tests/test_sqlite_storage.py tests/test_api_market_read.py` | `d131549` |
| 2A | 未留存 | `uv run pytest -q tests/test_notification_dispatcher.py` | `5320237`, `820e2db` |
| 2B | 未留存 | `uv run pytest -q tests/test_notification_dispatcher.py tests/test_notification_service.py` | `5320237` |
| 2C | 未留存 | `uv run pytest -q tests/test_notification_service.py tests/test_sqlite_storage.py` | `5320237` |
| 2D | 未留存 | `uv run pytest -q tests/test_api_notifications_audit.py tests/test_api_market_read.py tests/test_cli.py` | `5320237` |
| 2E | 未留存 | `uv run pytest -q tests/test_api_feedback.py tests/test_api_notifications_audit.py` | `5320237` |
| 3A | 未留存 | `uv run pytest -q tests/test_recommendation_repository.py tests/test_api_recommendations.py` | `5320237` |
| 3B | 未留存 | 同 3A | `5320237` |
| 4A | 未留存 | `uv run pytest -q tests/test_market_calendar.py tests/test_api_workflows.py` | `5320237` |
| 4B | 未留存 | `uv run pytest -q tests/test_market_backfill.py tests/test_api_workflows.py` | `5320237`, `3420944` |
| 4C | 未留存 | `uv run pytest -q tests/test_market_heavy_adapters.py tests/test_market_heavy_models.py` | `5320237` |
| 4D | 未留存 | `uv run pytest -q tests/test_market_backfill.py tests/test_market_heavy_models.py tests/test_decision_close_workflow.py` | `a98a49b`, `3420944` |
| 4E | 未留存 | `uv run pytest -q tests/test_market_heavy_repository.py tests/test_decision_close_workflow.py` | `6cfdc9e` |
| 4F | 未留存 | `uv run pytest -q tests/test_market_backfill.py tests/test_decision_close_workflow.py tests/test_cli.py` | `3420944` |
| 5A | 未留存 | `uv run pytest -q tests/test_decision_close_workflow.py` | `e1c5472` |
| 5B | 未留存 | 同 5A | `e1c5472` |
| 5C | 未留存 | `uv run pytest -q tests/test_decision_engine.py tests/test_decision_close_workflow.py` | `e1c5472` |
| 5D | 未留存 | `uv run pytest -q tests/test_strategy_service.py tests/test_decision_engine.py tests/test_decision_close_workflow.py` | `e1c5472` |
| 6A | 未留存 | `uv run pytest -q tests/test_decision_close_workflow.py tests/test_api_workflows.py` | `4c494c0` |
| 6B | 未留存 | 同 6A | `4c494c0` |
| 6C | 未留存 | `uv run pytest -q tests/test_api_workflows.py tests/test_cli.py tests/test_runtime_decision_workflow.py` | `4c494c0` |
| 6D | 未留存 | `uv run pytest -q tests/test_sqlite_storage.py tests/test_api_workflows.py` | `4c494c0` |
| 6E | 未留存 | `uv run pytest -q tests/test_decision_close_workflow.py` | `4c494c0` |
| 7A | 未留存 | `pnpm vitest run tests/market-refresh-coordinator.test.ts` | `1c2beea` |
| 7B | 未留存 | `pnpm vitest run tests/market-refresh-coordinator.test.ts tests/market-refresh-composable.test.ts` | `1c2beea` |
| 7C | 未留存 | `pnpm vitest run tests/market-refresh-coordinator.test.ts` | `1c2beea` |
| 7D | 未留存 | `pnpm vitest run tests/market-refresh-coordinator.test.ts tests/market-workbench.test.ts tests/monitoring.test.ts tests/recommendations.test.ts` | `1c2beea` |
| 7E | 未留存 | `pnpm vitest run tests/market-workbench.test.ts tests/monitoring.test.ts tests/recommendations.test.ts tests/query-hooks.test.ts tests/review-settings.test.ts` | `1c2beea` |

前端命令均在 `src/web` 下使用 Node `24.7.0` 和仓库锁定的 pnpm 执行。

## 独立审核

| 审核点 | 结论 | 修复或证据 |
| --- | --- | --- |
| Task 1-8 completion audit | 初次未通过：1 Medium、2 Low | 发现 Web 阶段明细缺失、验收记录缺失、plan identity 直接回归缺失；其余 Task 1-8 要求通过 |
| Web 阶段明细补充审核 | `FINAL PASS` | reviewer 独立运行 5 个聚焦文件 63 tests、typecheck、diff-check；提交 `a419c62` |
| plan identity 回归审核 | 首轮 1 Low，修复后 `FINAL PASS` | 增加三条建议 condition fingerprint 相等前置断言，排除 recommendation ID 虚假通过；提交 `820e2db` |
| 本验收记录 | `FINAL PASS` | reviewer 核对 31 个 Cycle、11 个提交边界、测试数字和证据缺口披露，无 finding |

## 已执行验证

### V1：Task 8 稳定基线

在 `e6e5b31` 完成后实际得到：

- `uv run pytest -q`：通过，进度到 `100%`，仅第三方弃用警告。
- Node 24 `pnpm test`：23 files，`160/160` tests 通过。
- Node 24 `pnpm build`：通过；仅既有约 885 KB chunk warning。
- Node 24 `pnpm e2e`：Chromium desktop/mobile `30/30` 通过。
- `/api/v1/service/status` 真实服务只读冒烟返回 200；未借用 token 的受保护行情读取按预期返回 401。
- `git diff --check`：通过；无 E2E 临时目录、临时服务进程或敏感运行数据。

### V2：completion audit 修复

- 阶段明细 RED：coordinator 新用例因 `onStageProgress` 0 次调用失败；三个页面用例均因不存在
  `行情刷新阶段详情` region 失败。
- 阶段明细 GREEN：4 个聚焦文件 `62/62` 通过，`vue-tsc --noEmit` 通过；reviewer
  额外包含 composable 后为 5 files、`63/63` 通过。
- plan identity characterization：新增 focused 用例首次即通过；完整
  `tests/test_notification_dispatcher.py` 为 `21/21` 通过。这是既有不变量的补覆盖，不是生产修复。
- `ruff` 未安装，命令返回无法 spawn；本文不把 lint 标为通过。

### V3：最终关闭验证

本记录通过独立审核后，于 2026-07-18 实际运行：

- `uv run pytest -q`：退出码 0，进度到 `100%`；仅有 Starlette/httpx 和 NumPy
  第三方弃用警告。
- Node 24 `pnpm test`：23 files、`161/161` tests 通过。
- Node 24 `pnpm build`：退出码 0，2454 modules transformed；产物 JS 888.36 KB，
  仅既有 chunk size warning。
- Node 24 `pnpm e2e`：Chromium desktop/mobile `30/30` 通过。
- 真实服务 `GET /api/v1/service/status` 返回 200 和 `auth_status=configured`；未借用 token 的
  `GET /api/v1/market/symbols` 按预期返回 401 `unauthorized`，没有写现场数据。
- `git diff --check`、受控敏感 token/private-key 模式扫描通过；没有跟踪 DB/SQLite/log/data
  文件，`data/` 仍被 `.gitignore` 忽略。
- 无 `market_refresh_e2e_server.py`、Playwright 或 `qt-market-refresh-e2e-*` 遗留。
  工作区已有一个运行 4 天的 Vite `:5173` 开发服务，确认不是本轮 E2E 产物并保留。
- HEAD author/committer 及仓库本地 Git 身份均为 `Equent <ryq2836@qq.com>`。
- 常驻 reviewer 最终 `FINAL PASS`：独立关键 Python 到 `100%`、完整 Vitest `161/161`、
  build 和静态清理检查通过，未发现 Task 1-8 或补修后的残余 requirement；没有重复主线程刚完成的
  Playwright `30/30`。
