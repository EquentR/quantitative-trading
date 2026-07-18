<script setup lang="ts">
import { computed } from 'vue'
import { Play, Square, RefreshCw } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Alert from '@/components/ui/Alert.vue'
import StatusBadges from '@/components/domain/StatusBadges.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import MarketRefreshStages from '@/components/domain/MarketRefreshStages.vue'
import { ApiError } from '@/api/client'
import {
  useServiceStatusQuery,
  useStartSchedulerMutation,
  useStopSchedulerMutation,
} from '@/queries/service'
import { useLatestSnapshotQuery } from '@/queries/account'
import { useMarketRunsQuery } from '@/queries/market'
import {
  marketRefreshErrorMessage,
  marketRefreshPhaseMessage,
  useMarketRefreshCoordinator,
} from '@/composables/useMarketRefreshCoordinator'

const serviceQuery = useServiceStatusQuery()
const snapshotQuery = useLatestSnapshotQuery()
const startMutation = useStartSchedulerMutation()
const stopMutation = useStopSchedulerMutation()
const marketRefresh = useMarketRefreshCoordinator()
const marketRunsQuery = useMarketRunsQuery()

const service = () => serviceQuery.data.value
const snapshot = () => snapshotQuery.data.value
const marketRuns = () => marketRunsQuery.data.value?.items ?? []

interface TaskRow {
  label: string
  job_id: string
  task_type: string
}

const taskRows: TaskRow[] = [
  { label: '盘中决策', job_id: 'decision_intraday', task_type: 'intraday' },
  { label: '收盘就绪', job_id: 'decision_close_readiness', task_type: 'close' },
  { label: '分钟清理', job_id: 'market_minute_cleanup', task_type: 'cleanup' },
  { label: '邮件投递', job_id: 'email_delivery_worker', task_type: 'email_delivery' },
]

const taskStatuses = computed(() => {
  const s = service()
  if (!s) return new Map<string, string>()
  const m = new Map<string, string>()
  for (const row of taskRows) {
    if (s.last_task_type === row.task_type && s.last_status) {
      m.set(row.task_type, s.last_status)
    }
  }
  return m
})

function taskStatusText(row: TaskRow): string {
  const s = service()
  if (!s) return '不可用'
  return taskStatuses.value.get(row.task_type) ?? '非最近运行'
}

const snapshotErrorMessage = () => {
  const error = snapshotQuery.error.value
  if (!(error instanceof ApiError)) return null
  if (error.code === 'snapshot_not_found') return '尚未生成账户快照'
  if (error.code === 'cash_account_not_initialized') return '手动资金账户尚未初始化'
  if (error.code === 'market_data_unavailable') return '行情不可用，账户估值不可作为完整数据'
  return error.message
}

const refreshLabel = computed(() =>
  marketRefreshPhaseMessage(marketRefresh.phase.value) || '刷新行情数据',
)
const refreshError = computed(() => marketRefreshErrorMessage(marketRefresh.error.value))

function formatDuration(value: number | null): string {
  if (value === null) return '进行中'
  if (value < 1000) return `${Math.round(value)} ms`
  return `${(value / 1000).toFixed(1)} s`
}

const workflowLabels: Record<string, string> = {
  close: '收盘',
  intraday: '盘中',
  backfill: '回填',
  cleanup: '清理',
}

const datasetLabels: Record<string, string> = {
  quote: 'quote',
  daily_bar: 'daily_bar',
  money_flow: 'money_flow',
  minute_bar: 'minute_bar',
  intraday_strength: 'intraday_strength',
}

function formatDatasetCounts(run: ReturnType<typeof marketRuns>[number]): string[] {
  return Object.entries(run.dataset_counts ?? {}).map(([dataset, counts]) => {
    const values = [
      counts.complete ? `完成 ${counts.complete}` : '',
      counts.degraded ? `降级 ${counts.degraded}` : '',
      counts.failed ? `失败 ${counts.failed}` : '',
      counts.stale ? `陈旧 ${counts.stale}` : '',
    ].filter(Boolean)
    return `${datasetLabels[dataset] ?? dataset} ${values.join(' / ')}`
  })
}

async function onGenerate() {
  try {
    await marketRefresh.run()
  } catch {
    // The coordinator exposes a sanitized, stage-aware message for the page.
  }
}
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold">监控</h1>
    <p class="text-sm text-muted-foreground">账户快照调度仅采集行情与台账数据，不执行真实交易。</p>

    <section class="space-y-2">
      <h2 class="text-sm font-medium">调度状态</h2>
      <StatusBadges
        :auth-status="service()?.auth_status"
        :scheduler-running="service()?.scheduler_running"
      />
      <p v-if="service()" class="text-xs text-muted-foreground break-words">
        最近原因：{{ service()?.last_reason ?? '-' }} · 并发超限 {{ service()?.overrun_count ?? 0 }} 次 · 跳过 {{ service()?.skipped_count ?? 0 }} 次
      </p>
      <div class="flex flex-wrap gap-2">
        <Button variant="primary" :loading="startMutation.isPending.value" @click="startMutation.mutate()">
          <Play class="size-4" />
          启动工作流调度
        </Button>
        <Button variant="danger" :loading="stopMutation.isPending.value" @click="stopMutation.mutate()">
          <Square class="size-4" />
          停止工作流调度
        </Button>
        <Button
          class="w-44 shrink-0"
          variant="secondary"
          :loading="marketRefresh.isPending.value"
          :aria-label="refreshLabel"
          @click="onGenerate"
        >
          <RefreshCw v-if="!marketRefresh.isPending.value" class="size-4" />
          {{ refreshLabel }}
        </Button>
      </div>
      <Alert v-if="marketRefresh.hasFailed.value" variant="danger">
        {{ marketRefresh.message.value }}
      </Alert>
      <p v-else-if="marketRefresh.message.value" class="text-sm text-emerald-700" role="status">
        {{ marketRefresh.message.value }}
      </p>
      <Alert v-if="refreshError" :variant="marketRefresh.error.value?.name === 'MarketRefreshPendingError' ? 'warning' : 'danger'">
        {{ refreshError }}
      </Alert>
      <MarketRefreshStages :stages="marketRefresh.stageProgress.value" />
    </section>

    <section class="space-y-2">
      <div class="flex items-center justify-between gap-2">
        <h2 class="text-sm font-medium">工作流运行</h2>
        <span class="text-xs text-muted-foreground">最近 {{ marketRuns().length }} 轮</span>
      </div>
      <Alert v-if="marketRunsQuery.isError.value" variant="warning">运行记录加载失败</Alert>
      <div v-else class="overflow-x-auto border-y border-border">
        <table class="min-w-[1720px] w-full text-sm">
          <thead class="text-left text-xs text-muted-foreground">
            <tr>
              <th class="py-2 pr-3">运行时间</th>
              <th class="py-2 pr-3">工作流</th>
              <th class="py-2 pr-3">周期</th>
              <th class="py-2 pr-3">标的</th>
              <th class="py-2 pr-3">状态</th>
              <th class="py-2 pr-3">总耗时</th>
              <th class="py-2 pr-3">Provider</th>
              <th class="py-2 pr-3">数据行</th>
              <th class="py-2 pr-3">产物</th>
              <th class="py-2 pr-3">重试</th>
              <th class="py-2 pr-3">数据集</th>
              <th class="py-2">告警 / 错误</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="marketRuns().length === 0" class="border-t border-border">
              <td colspan="12" class="py-3 text-muted-foreground">暂无工作流运行记录</td>
            </tr>
            <tr
              v-for="run in marketRuns()"
              :key="run.run_id"
              class="border-t border-border align-top"
            >
              <td class="py-2 pr-3 whitespace-nowrap">
                <div>开始 <FormatValues kind="time" :value="run.started_at" /></div>
                <div class="text-xs text-muted-foreground">结束 <FormatValues kind="time" :value="run.finished_at" /></div>
              </td>
              <td class="py-2 pr-3">
                <div>{{ workflowLabels[run.workflow_type] ?? run.workflow_type }}</div>
                <div class="text-xs text-muted-foreground">交易日 {{ run.trade_date }}</div>
                <div class="text-xs text-muted-foreground">模式 {{ run.mode ?? '-' }}</div>
                <div class="text-xs text-muted-foreground">有效交易日 {{ run.effective_trade_date ?? '-' }}</div>
                <div class="text-xs text-muted-foreground">历史截止 {{ run.history_cutoff_date ?? '-' }}</div>
                <div class="max-w-56 truncate text-xs text-muted-foreground" :title="run.run_id">{{ run.run_id }}</div>
                <div class="max-w-64 truncate text-xs text-muted-foreground" :title="run.idempotency_key">{{ run.idempotency_key }}</div>
              </td>
              <td class="py-2 pr-3 whitespace-nowrap text-xs">
                <div><FormatValues kind="time" :value="run.period_start" /></div>
                <div class="text-muted-foreground"><FormatValues kind="time" :value="run.period_end" /></div>
              </td>
              <td class="py-2 pr-3 text-xs">
                <div class="whitespace-nowrap">请求 {{ run.requested_symbols }} / 完成 {{ run.processed_symbols }}</div>
                <div class="max-w-72 break-words text-muted-foreground">范围 {{ run.requested_symbol_scope.join(', ') || '-' }}</div>
                <div class="whitespace-nowrap text-muted-foreground">租约 <FormatValues kind="time" :value="run.lease_expires_at" /></div>
              </td>
              <td class="py-2 pr-3">{{ run.status }}</td>
              <td class="py-2 pr-3 whitespace-nowrap">{{ formatDuration(run.duration_ms) }}</td>
              <td class="py-2 pr-3 whitespace-nowrap">{{ run.provider_calls }} 次 / {{ formatDuration(run.provider_duration_ms) }}</td>
              <td class="py-2 pr-3 whitespace-nowrap">收 {{ run.rows_received }} / 写 {{ run.rows_written }} / 清 {{ run.cleaned_rows }}</td>
              <td class="py-2 pr-3 whitespace-nowrap">计划 {{ run.plan_count }} / 建议 {{ run.recommendation_count }} / 通知 {{ run.notification_count }} / 邮件 {{ run.email_outbox_count }}</td>
              <td class="py-2 pr-3">{{ run.retry_count }}</td>
              <td class="py-2 pr-3 text-xs whitespace-nowrap">
                <div v-for="line in formatDatasetCounts(run)" :key="line">{{ line }}</div>
                <span v-if="formatDatasetCounts(run).length === 0" class="text-muted-foreground">-</span>
              </td>
              <td class="max-w-64 py-2 break-words">
                <span v-if="run.error_summary">{{ run.error_summary }}</span>
                <span v-else-if="run.warning_count || run.failure_count">warning {{ run.warning_count }} / failed {{ run.failure_count }}</span>
                <span v-else class="text-muted-foreground">-</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="space-y-2">
      <h2 class="text-sm font-medium">任务状态</h2>
      <p class="text-xs text-muted-foreground">仅显示全局最近一次任务结果；其他任务标记为非最近运行。</p>
      <table class="w-full table-fixed text-sm">
        <thead class="text-left text-xs text-muted-foreground">
          <tr>
            <th class="w-1/3 py-1">任务</th>
            <th class="w-1/4 py-1">调度 Job ID</th>
            <th class="w-1/4 py-1">最近状态</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in taskRows"
            :key="row.task_type"
            class="border-t border-border align-top break-words"
          >
            <td class="py-1.5">{{ row.label }}</td>
            <td class="py-1.5 text-xs text-muted-foreground break-all">{{ row.job_id }}</td>
            <td class="py-1.5 break-words">{{ taskStatusText(row) }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="space-y-2">
      <h2 class="text-sm font-medium">最近错误与数据缺口</h2>
      <Alert v-if="service()?.last_error" variant="warning">
        <p class="break-words">最近错误：{{ service()?.last_error }}</p>
      </Alert>
      <Alert v-if="snapshot() && snapshot()?.status !== 'ok'" variant="warning">
        <div class="space-y-1">
          <p>快照数据不完整，请检查行情或资金账户状态。</p>
          <ul v-if="snapshot()?.warnings.length" class="list-disc pl-4 break-words">
            <li v-for="warning in snapshot()?.warnings" :key="warning">{{ warning }}</li>
          </ul>
        </div>
      </Alert>
      <Alert v-if="snapshotErrorMessage()" variant="warning">
        {{ snapshotErrorMessage() }}
      </Alert>
    </section>

    <section class="space-y-2">
      <h2 class="text-sm font-medium">最新快照</h2>
      <div class="space-y-1 text-sm">
        <p>快照时间：<FormatValues kind="time" :value="snapshot()?.created_at" /></p>
        <p>总资产：<FormatValues kind="money" :value="snapshot()?.total_assets" /></p>
        <p>仓位比例：<FormatValues kind="ratio" :value="snapshot()?.position_ratio" /></p>
      </div>
    </section>
  </div>
</template>
