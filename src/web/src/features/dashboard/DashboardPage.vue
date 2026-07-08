<script setup lang="ts">
import { computed } from 'vue'
import { useServiceStatusQuery } from '@/queries/service'
import { useLatestSnapshotQuery } from '@/queries/account'
import { usePositionsQuery } from '@/queries/positions'
import { useCashAccountQuery } from '@/queries/cash'
import { ApiError } from '@/api/client'
import StatusBadges from '@/components/domain/StatusBadges.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import Alert from '@/components/ui/Alert.vue'

const serviceQuery = useServiceStatusQuery()
const snapshotQuery = useLatestSnapshotQuery()
const positionsQuery = usePositionsQuery()
const cashQuery = useCashAccountQuery()

const service = computed(() => serviceQuery.data.value)
const snapshot = computed(() => snapshotQuery.data.value)
const positions = computed(() => positionsQuery.data.value)
const cash = computed(() => cashQuery.data.value)

const snapshotWarning = computed(() => {
  if (!snapshot.value) return false
  return snapshot.value.status !== 'ok'
})

const snapshotNotFound = computed(() => {
  const error = snapshotQuery.error.value
  return error instanceof ApiError && error.code === 'snapshot_not_found'
})

const snapshotErrorMessage = computed(() => {
  const error = snapshotQuery.error.value
  if (!(error instanceof ApiError)) return null
  if (error.code === 'snapshot_not_found') return '尚未生成账户快照'
  if (error.code === 'cash_account_not_initialized') return '手动资金账户尚未初始化'
  if (error.code === 'market_data_unavailable') return '行情不可用，账户估值不可作为完整数据'
  return error.message
})
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold">今日仪表盘</h1>

    <Alert v-if="snapshotWarning" variant="warning">
      <div class="space-y-1">
        <p>快照数据不完整，账户估值仅供参考，请检查行情或资金账户状态。</p>
        <ul v-if="snapshot?.warnings.length" class="list-disc pl-4">
          <li v-for="warning in snapshot.warnings" :key="warning">{{ warning }}</li>
        </ul>
      </div>
    </Alert>

    <Alert v-if="snapshotErrorMessage" variant="warning">
      {{ snapshotErrorMessage }}
    </Alert>

    <section>
      <h2 class="mb-2 text-sm font-medium">服务与调度</h2>
      <div class="space-y-1 text-sm">
        <StatusBadges
          :auth-status="service?.auth_status"
          :scheduler-running="service?.scheduler_running"
        />
        <p>调度间隔：{{ service?.interval_seconds ? `${service.interval_seconds} 秒` : '不可用' }}</p>
        <p v-if="service?.last_status">最近运行：{{ service.last_status }}</p>
      </div>
    </section>

    <section>
      <h2 class="mb-2 text-sm font-medium">账户估值</h2>
      <div class="space-y-1 text-sm">
        <p>现金余额：<FormatValues kind="money" :value="snapshotNotFound ? null : snapshot?.cash_balance" /></p>
        <p>持仓市值：<FormatValues kind="money" :value="snapshotNotFound ? null : snapshot?.market_value" /></p>
        <p>总资产：<FormatValues kind="money" :value="snapshotNotFound ? null : snapshot?.total_assets" /></p>
        <p>仓位比例：<FormatValues kind="ratio" :value="snapshotNotFound ? null : snapshot?.position_ratio" /></p>
      </div>
    </section>

    <section>
      <h2 class="mb-2 text-sm font-medium">持仓摘要</h2>
      <div class="space-y-1 text-sm">
        <p>持仓数量：{{ positions?.length ?? 0 }}</p>
        <ul v-if="positions?.length" class="space-y-0.5">
          <li v-for="p in positions" :key="p.symbol">
            {{ p.symbol }} {{ p.name }} {{ p.quantity }} 股
          </li>
        </ul>
      </div>
    </section>

    <section>
      <h2 class="mb-2 text-sm font-medium">资金摘要</h2>
      <div class="space-y-1 text-sm">
        <p>现金余额：<FormatValues kind="money" :value="cash?.cash_balance" /></p>
        <p>净本金：<FormatValues kind="money" :value="cash?.net_principal" /></p>
        <p>更新时间：<FormatValues kind="time" :value="cash?.updated_at" /></p>
      </div>
    </section>
  </div>
</template>
