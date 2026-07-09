<script setup lang="ts">
import { computed, ref } from 'vue'
import { ScanLine } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Alert from '@/components/ui/Alert.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import RecommendationStatusBadge from '@/components/domain/RecommendationStatusBadge.vue'
import RecommendationDetailDrawer from './RecommendationDetailDrawer.vue'
import { useRecommendationsQuery, useScanRecommendationsMutation } from '@/queries/recommendations'
import { useNotificationsQuery } from '@/queries/notifications'
import type { Recommendation, NotificationProcessingStatus } from '@/api/types'

const recommendationsQuery = useRecommendationsQuery()
const notificationsQuery = useNotificationsQuery()
const scanMutation = useScanRecommendationsMutation()

const recommendations = computed(() => recommendationsQuery.data.value ?? [])
const notifications = computed(() => notificationsQuery.data.value ?? [])
const notificationsError = computed(() => notificationsQuery.error.value != null)

const statusByRec = computed(() => {
  const m = new Map<string, NotificationProcessingStatus>()
  for (const n of notifications.value) {
    if (n.recommendation_id) m.set(n.recommendation_id, n.status)
  }
  return m
})

const statusFilter = ref<'all' | NotificationProcessingStatus>('all')
const showFilter = computed(
  () => !notificationsError.value && notifications.value.length > 0,
)

const filteredRecommendations = computed(() => {
  if (statusFilter.value === 'all') return recommendations.value
  return recommendations.value.filter(
    (r) => statusByRec.value.get(r.recommendation_id) === statusFilter.value,
  )
})

const scanError = ref(false)

const selectedId = ref<string | null>(null)
const selectedRecommendation = computed(
  () => recommendations.value.find((r) => r.recommendation_id === selectedId.value) ?? null,
)

function openDetail(id: string) {
  selectedId.value = id
}
function closeDetail() {
  selectedId.value = null
}

async function onScan() {
  scanError.value = false
  try {
    await scanMutation.mutateAsync()
  } catch {
    scanError.value = true
  }
}

function keyPriceText(r: Recommendation): string {
  const pc = (r.price_context ?? {}) as Record<string, unknown>
  const parts: string[] = []
  if (typeof pc.current_price === 'number') parts.push(`现价 ${pc.current_price}`)
  const kl = (pc.key_levels ?? {}) as Record<string, unknown>
  if (typeof kl.support === 'number') parts.push(`支撑 ${kl.support}`)
  if (typeof kl.resistance === 'number') parts.push(`阻力 ${kl.resistance}`)
  if (typeof kl.stop_loss === 'number') parts.push(`止损 ${kl.stop_loss}`)
  return parts.join(' / ')
}
</script>

<template>
  <div class="space-y-4">
    <div class="flex items-center justify-between gap-2">
      <h1 class="text-lg font-semibold">建议</h1>
      <Button variant="primary" :loading="scanMutation.isPending.value" @click="onScan">
        <ScanLine class="size-4" />
        扫描建议
      </Button>
    </div>
    <p class="text-sm text-muted-foreground">建议仅用于本地决策辅助，需人工确认后手动执行，不自动真实下单。</p>

    <Alert v-if="recommendationsQuery.error.value" variant="danger">
      建议数据加载失败，请稍后重试或检查后端服务状态。
    </Alert>

    <Alert v-if="scanError" variant="danger" data-testid="scan-error-alert">
      扫描建议失败，请稍后重试或检查后端服务状态。
    </Alert>

    <div v-if="showFilter" class="flex items-center gap-2 text-sm">
      <label for="rec-status-filter" class="whitespace-nowrap">处理状态筛选</label>
      <select
        id="rec-status-filter"
        v-model="statusFilter"
        class="rounded-md border border-border px-2 py-1 text-sm"
      >
        <option value="all">全部</option>
        <option value="unread">未读</option>
        <option value="read">已读</option>
        <option value="feedback_recorded">已记录反馈</option>
      </select>
    </div>

    <div v-if="filteredRecommendations.length" class="overflow-x-auto">
      <table class="w-full table-fixed text-sm">
        <thead class="text-left text-xs text-muted-foreground">
          <tr>
            <th class="w-1/4 py-1">股票</th>
            <th class="w-[8%] py-1">动作</th>
            <th class="w-[8%] py-1">置信度</th>
            <th class="w-[10%] py-1">处理状态</th>
            <th class="w-1/4 py-1">关键价位</th>
            <th class="w-1/5 py-1">数据时间</th>
            <th class="py-1 text-right">详情</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in filteredRecommendations"
            :key="r.recommendation_id"
            class="border-t border-border align-top break-words"
          >
            <td class="py-1.5">
              <div>{{ r.symbol }}</div>
              <div class="text-xs text-muted-foreground">{{ r.name }}</div>
            </td>
            <td class="py-1.5"><RecommendationStatusBadge kind="action" :value="r.action" /></td>
            <td class="py-1.5"><RecommendationStatusBadge kind="confidence" :value="r.confidence" /></td>
            <td class="py-1.5">
              <RecommendationStatusBadge
                v-if="statusByRec.get(r.recommendation_id)"
                kind="status"
                :value="statusByRec.get(r.recommendation_id)!"
              />
              <span v-else class="text-xs text-muted-foreground">不可用</span>
            </td>
            <td class="py-1.5 text-xs whitespace-normal break-words">{{ keyPriceText(r) }}</td>
            <td class="py-1.5 text-xs"><FormatValues kind="time" :value="r.data_time" /></td>
            <td class="py-1.5 text-right">
              <Button
                variant="ghost"
                :aria-label="`查看详情 ${r.symbol}`"
                @click="openDetail(r.recommendation_id)"
              >
                查看详情
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-else class="text-sm text-muted-foreground">暂无建议，点击"扫描建议"生成。</p>

    <RecommendationDetailDrawer
      v-if="selectedRecommendation"
      :recommendation="selectedRecommendation"
      @close="closeDetail"
    />
  </div>
</template>
