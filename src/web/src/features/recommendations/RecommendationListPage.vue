<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { RefreshCw } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Alert from '@/components/ui/Alert.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import MarketRefreshStages from '@/components/domain/MarketRefreshStages.vue'
import RecommendationStatusBadge from '@/components/domain/RecommendationStatusBadge.vue'
import RecommendationDetailDrawer from './RecommendationDetailDrawer.vue'
import { useRecommendationQuery, useRecommendationsQuery } from '@/queries/recommendations'
import type {
  Recommendation,
  RecommendationListItem,
  NotificationProcessingStatus,
  RecommendationView,
} from '@/api/types'
import {
  marketRefreshErrorMessage,
  marketRefreshPhaseMessage,
  useMarketRefreshCoordinator,
} from '@/composables/useMarketRefreshCoordinator'

const route = useRoute()
const router = useRouter()
const selectedView = ref<RecommendationView>('current')
const recommendationsQuery = useRecommendationsQuery(selectedView)
const marketRefresh = useMarketRefreshCoordinator()
const requestedRecommendationId = computed(() => {
  const value = Array.isArray(route.query.recommendation_id)
    ? route.query.recommendation_id[0]
    : route.query.recommendation_id
  return typeof value === 'string' && value ? value : null
})
const requestedRecommendationQuery = useRecommendationQuery(requestedRecommendationId)

const items = computed(() => recommendationsQuery.data.value ?? [])
const recommendations = computed(() => items.value.map((item) => item.recommendation))

const statusByRec = computed(() => {
  const m = new Map<string, NotificationProcessingStatus>()
  for (const item of items.value) {
    if (item.notification) {
      m.set(item.recommendation.recommendation_id, item.notification.status)
    }
  }
  return m
})

const statusFilter = ref<'all' | NotificationProcessingStatus>('all')
const showFilter = computed(
  () => items.value.some((item) => item.notification !== null),
)

const filteredRecommendations = computed(() => {
  if (!showFilter.value || statusFilter.value === 'all') return recommendations.value
  return recommendations.value.filter(
    (r) => statusByRec.value.get(r.recommendation_id) === statusFilter.value,
  )
})

const refreshLabel = computed(() =>
  marketRefreshPhaseMessage(marketRefresh.phase.value) || '刷新行情与建议',
)
const refreshError = computed(() => marketRefreshErrorMessage(marketRefresh.error.value))

const selectedId = ref<string | null>(null)
const selectedRecommendation = computed(
  () => recommendations.value.find((r) => r.recommendation_id === selectedId.value)
    ?? (requestedRecommendationQuery.data.value?.recommendation_id === selectedId.value
      ? requestedRecommendationQuery.data.value
      : null),
)
const selectedItem = computed<RecommendationListItem | null>(() =>
  items.value.find(
    (item) => item.recommendation.recommendation_id === selectedRecommendation.value?.recommendation_id,
  ) ?? (selectedRecommendation.value
    ? { recommendation: selectedRecommendation.value, notification: null }
    : null),
)

watch(requestedRecommendationId, (recommendationId) => {
  selectedId.value = recommendationId
}, { immediate: true })

watch(selectedView, () => {
  statusFilter.value = 'all'
  closeDetail()
})

function openDetail(id: string) {
  selectedId.value = id
  void router.replace({ query: { ...route.query, recommendation_id: id } })
}
function closeDetail() {
  selectedId.value = null
  if (route.query.recommendation_id === undefined) return
  const query = { ...route.query }
  delete query.recommendation_id
  void router.replace({ query })
}

async function onScan() {
  try {
    await marketRefresh.run()
  } catch {
    // The coordinator exposes a sanitized, stage-aware message for the page.
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
      <Button class="w-44 shrink-0" variant="primary" :loading="marketRefresh.isPending.value" @click="onScan">
        <RefreshCw v-if="!marketRefresh.isPending.value" class="size-4" />
        {{ refreshLabel }}
      </Button>
    </div>
    <p class="text-sm text-muted-foreground">建议仅用于本地决策辅助，需人工确认后手动执行，不自动真实下单。</p>

    <Alert v-if="recommendationsQuery.error.value" variant="danger">
      建议数据加载失败，请稍后重试或检查后端服务状态。
    </Alert>

    <Alert v-if="refreshError" :variant="marketRefresh.error.value?.name === 'MarketRefreshPendingError' ? 'warning' : 'danger'" data-testid="scan-error-alert">
      {{ refreshError }}
    </Alert>
    <Alert v-if="marketRefresh.hasFailed.value" variant="danger">{{ marketRefresh.message.value }}</Alert>
    <p v-else-if="marketRefresh.message.value" class="text-sm text-emerald-700" role="status">{{ marketRefresh.message.value }}</p>
    <MarketRefreshStages :stages="marketRefresh.stageProgress.value" />

    <div class="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="建议视图">
      <button
        v-for="option in ([['current', '当前状态'], ['history', '历史记录']] as const)"
        :key="option[0]"
        type="button"
        class="min-w-24 rounded px-3 py-1.5 text-sm"
        :class="selectedView === option[0] ? 'bg-primary text-primary-foreground' : 'hover:bg-muted'"
        :aria-pressed="selectedView === option[0]"
        @click="selectedView = option[0]"
      >
        {{ option[1] }}
      </button>
    </div>

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
      <table class="min-w-[760px] w-full table-fixed text-sm">
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
    <p v-else class="text-sm text-muted-foreground">当前视图暂无建议。</p>

    <RecommendationDetailDrawer
      v-if="selectedItem"
      :recommendation="selectedItem.recommendation"
      :notification="selectedItem.notification"
      @close="closeDetail"
    />
  </div>
</template>
