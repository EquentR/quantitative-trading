<script setup lang="ts">
import { computed, ref } from 'vue'
import { Play, Square, Camera } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Alert from '@/components/ui/Alert.vue'
import StatusBadges from '@/components/domain/StatusBadges.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import { ApiError } from '@/api/client'
import {
  useRunOnceMutation,
  useServiceStatusQuery,
  useStartSchedulerMutation,
  useStopSchedulerMutation,
} from '@/queries/service'
import { useLatestSnapshotQuery } from '@/queries/account'

const serviceQuery = useServiceStatusQuery()
const snapshotQuery = useLatestSnapshotQuery()
const startMutation = useStartSchedulerMutation()
const stopMutation = useStopSchedulerMutation()
const runOnceMutation = useRunOnceMutation()

const service = () => serviceQuery.data.value
const snapshot = () => snapshotQuery.data.value

interface TaskRow {
  label: string
  job_id: string
  task_type: string
}

const taskRows: TaskRow[] = [
  { label: '账户快照任务', job_id: 'account_snapshot_intraday', task_type: 'account_snapshot' },
  { label: '收盘计划任务', job_id: 'close_plan_daily', task_type: 'close_plan_daily' },
  { label: '盘中触发任务', job_id: 'recommendation_intraday_trigger', task_type: 'recommendation_intraday_trigger' },
]

const taskStatuses = computed(() => {
  const s = service()
  if (!s) return new Map<string, string>()
  const m = new Map<string, string>()
  if (s.last_task_type === taskRows[0].task_type && s.last_status) {
    m.set(taskRows[0].task_type, s.last_status)
  }
  if (s.last_task_type === taskRows[1].task_type && s.last_status) {
    m.set(taskRows[1].task_type, s.last_status)
  }
  if (s.last_task_type === taskRows[2].task_type && s.last_status) {
    m.set(taskRows[2].task_type, s.last_status)
  }
  return m
})

const snapshotErrorMessage = () => {
  const error = snapshotQuery.error.value
  if (!(error instanceof ApiError)) return null
  if (error.code === 'snapshot_not_found') return '尚未生成账户快照'
  if (error.code === 'cash_account_not_initialized') return '手动资金账户尚未初始化'
  if (error.code === 'market_data_unavailable') return '行情不可用，账户估值不可作为完整数据'
  return error.message
}

const runMessage = ref('')

async function onGenerate() {
  await runOnceMutation.mutateAsync()
  runMessage.value = '已请求生成账户快照'
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
      <div class="flex flex-wrap gap-2">
        <Button variant="primary" :loading="startMutation.isPending.value" @click="startMutation.mutate()">
          <Play class="size-4" />
          启动账户快照调度
        </Button>
        <Button variant="danger" :loading="stopMutation.isPending.value" @click="stopMutation.mutate()">
          <Square class="size-4" />
          停止账户快照调度
        </Button>
        <Button variant="secondary" :loading="runOnceMutation.isPending.value" @click="onGenerate">
          <Camera class="size-4" />
          生成一次账户快照
        </Button>
      </div>
      <p v-if="runMessage" class="text-sm text-emerald-700">{{ runMessage }}</p>
    </section>

    <section class="space-y-2">
      <h2 class="text-sm font-medium">任务状态</h2>
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
            <td class="py-1.5 break-words">{{ taskStatuses.get(row.task_type) ?? '不可用' }}</td>
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
