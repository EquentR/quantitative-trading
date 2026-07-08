<script setup lang="ts">
import { ref } from 'vue'
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
      <p v-if="service()?.last_error" class="text-sm text-red-700">最近错误：{{ service()?.last_error }}</p>
    </section>

    <section class="space-y-2">
      <h2 class="text-sm font-medium">最新快照</h2>
      <Alert v-if="snapshot() && snapshot()?.status !== 'ok'" variant="warning">
        <div class="space-y-1">
          <p>快照数据不完整，请检查行情或资金账户状态。</p>
          <ul v-if="snapshot()?.warnings.length" class="list-disc pl-4">
            <li v-for="warning in snapshot()?.warnings" :key="warning">{{ warning }}</li>
          </ul>
        </div>
      </Alert>
      <Alert v-if="snapshotErrorMessage()" variant="warning">
        {{ snapshotErrorMessage() }}
      </Alert>
      <div class="space-y-1 text-sm">
        <p>快照时间：<FormatValues kind="time" :value="snapshot()?.created_at" /></p>
        <p>总资产：<FormatValues kind="money" :value="snapshot()?.total_assets" /></p>
        <p>仓位比例：<FormatValues kind="ratio" :value="snapshot()?.position_ratio" /></p>
      </div>
    </section>
  </div>
</template>
