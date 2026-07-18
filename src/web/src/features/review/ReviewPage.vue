<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import Alert from '@/components/ui/Alert.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import RecommendationStatusBadge from '@/components/domain/RecommendationStatusBadge.vue'
import { useRecommendationsQuery } from '@/queries/recommendations'
import { useNotificationsQuery } from '@/queries/notifications'
import { useLatestPlanQuery, usePlanQuery } from '@/queries/plans'
import AuditLogPanel from './AuditLogPanel.vue'
import FeedbackPanel from './FeedbackPanel.vue'
import type { Recommendation, RecommendationView } from '@/api/types'

const route = useRoute()
const selectedView = ref<RecommendationView>('current')
const recommendationsQuery = useRecommendationsQuery(selectedView)
const notificationsQuery = useNotificationsQuery(selectedView)
const planQuery = useLatestPlanQuery()
const requestedPlanId = computed(() => {
  const value = Array.isArray(route.query.plan_id) ? route.query.plan_id[0] : route.query.plan_id
  return typeof value === 'string' && value ? value : null
})
const requestedPlanQuery = usePlanQuery(requestedPlanId)

const recommendations = computed(() =>
  (recommendationsQuery.data.value ?? []).map((item) => item.recommendation),
)
const notifications = computed(() => notificationsQuery.data.value ?? [])
const plan = computed(() => requestedPlanId.value
  ? requestedPlanQuery.data.value
  : planQuery.data.value)
const planSymbols = computed(() => Array.from(new Set([
  ...(plan.value?.holding_symbols ?? []),
  ...(plan.value?.watch_symbols ?? []),
])))
const notificationsError = computed(() => notificationsQuery.error.value != null)

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

function listText(value: unknown, fallback = '不可用'): string {
  if (!Array.isArray(value)) return fallback
  const items = value.map((item) => String(item)).filter(Boolean)
  return items.length ? items.join(' / ') : fallback
}

function riskText(r: Recommendation): string {
  const risk = (r.risk ?? {}) as Record<string, unknown>
  const parts: string[] = []
  if (risk.position_limit) parts.push(String(risk.position_limit))
  if (Array.isArray(risk.notes)) {
    parts.push(...risk.notes.map((item) => String(item)).filter(Boolean))
  }
  return parts.length ? parts.join(' / ') : '不可用'
}

function invalidIfText(r: Recommendation): string {
  return listText(((r.risk ?? {}) as Record<string, unknown>).invalid_if)
}
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold">复盘</h1>
    <p class="text-sm text-muted-foreground">复盘用于决策回顾与反馈记录，不自动真实下单，不代表收益保证。</p>

    <div class="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="复盘视图">
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

    <section class="space-y-2">
      <h2 class="text-sm font-medium">推荐记录</h2>
      <Alert v-if="recommendationsQuery.error.value" variant="warning">
        <p>推荐数据不可用</p>
      </Alert>
      <div v-if="recommendations.length" class="overflow-x-auto">
        <table class="min-w-[1040px] w-full table-fixed text-xs">
          <thead class="text-left text-muted-foreground">
            <tr>
              <th class="w-1/5 py-1">股票</th>
              <th class="w-[8%] py-1">动作</th>
              <th class="w-[8%] py-1">置信度</th>
              <th class="w-1/4 py-1">关键价位</th>
              <th class="w-1/4 py-1">理由</th>
              <th class="w-1/4 py-1">风险</th>
              <th class="w-1/4 py-1">失效条件</th>
              <th class="w-1/5 py-1">数据时间</th>
              <th class="w-1/5 py-1">有效期</th>
              <th class="w-1/5 py-1">建议ID</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="r in recommendations"
              :key="r.recommendation_id"
              class="border-t border-border align-top break-words"
            >
              <td class="py-1.5">
                <RouterLink
                  class="text-primary underline"
                  :to="{ path: '/market', query: { symbol: r.symbol } }"
                  :aria-label="`返回 ${r.symbol} 行情（建议 ${r.recommendation_id}）`"
                >{{ r.symbol }}</RouterLink>
                <div class="text-muted-foreground">{{ r.name }}</div>
              </td>
              <td class="py-1.5"><RecommendationStatusBadge kind="action" :value="r.action" /></td>
              <td class="py-1.5"><RecommendationStatusBadge kind="confidence" :value="r.confidence" /></td>
              <td class="py-1.5 break-words whitespace-normal">{{ keyPriceText(r) }}</td>
              <td class="py-1.5 break-words whitespace-normal">{{ listText(r.reason) }}</td>
              <td class="py-1.5 break-words whitespace-normal">{{ riskText(r) }}</td>
              <td class="py-1.5 break-words whitespace-normal">{{ invalidIfText(r) }}</td>
              <td class="py-1.5"><FormatValues kind="time" :value="r.data_time" /></td>
              <td class="py-1.5"><FormatValues kind="time" :value="r.valid_until" /></td>
              <td class="py-1.5 break-words">{{ r.recommendation_id }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!recommendations.length && !recommendationsQuery.error.value" class="text-xs text-muted-foreground">
        加载中或暂无推荐记录
      </p>
    </section>

    <FeedbackPanel />

    <AuditLogPanel />

    <section class="space-y-2">
      <h2 class="text-sm font-medium">通知摘要</h2>
      <Alert v-if="notificationsError" variant="warning">
        <p>通知数据不可用</p>
      </Alert>
      <div v-if="!notificationsError && notifications.length" class="overflow-x-auto">
        <table class="min-w-[680px] w-full table-fixed text-xs">
          <thead class="text-left text-muted-foreground">
            <tr>
              <th class="w-1/5 py-1">股票</th>
              <th class="w-[8%] py-1">动作</th>
              <th class="w-[10%] py-1">状态</th>
              <th class="w-1/4 py-1">理由</th>
              <th class="w-1/5 py-1">数据时间</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="n in notifications"
              :key="n.notification_id"
              class="border-t border-border align-top break-words"
            >
              <td class="py-1.5">
                <RouterLink
                  class="text-primary underline"
                  :to="{ path: '/market', query: { symbol: n.symbol } }"
                  :aria-label="`返回 ${n.symbol} 行情（通知 ${n.notification_id}）`"
                >{{ n.symbol }}</RouterLink>
              </td>
              <td class="py-1.5">{{ n.action }}</td>
              <td class="py-1.5"><RecommendationStatusBadge kind="status" :value="n.status" /></td>
              <td class="py-1.5 break-words whitespace-normal">{{ n.reason.join(' / ') }}</td>
              <td class="py-1.5"><FormatValues kind="time" :value="n.data_time" /></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="!notificationsError && !notifications.length" class="text-xs text-muted-foreground">
        加载中或暂无通知
      </p>
    </section>

    <section
      class="space-y-2"
      :class="requestedPlanId && plan?.plan_id === requestedPlanId ? 'border-l-2 border-primary pl-3' : ''"
      :data-plan-id="plan?.plan_id"
    >
      <h2 class="text-sm font-medium">输入快照引用</h2>
      <div class="space-y-1 text-sm">
        <template v-if="plan">
          <p v-if="requestedPlanId === plan.plan_id" class="font-medium text-primary" role="status">
            已定位计划 {{ plan.plan_id }}
          </p>
          <p>计划ID：{{ plan.plan_id }}</p>
          <p>Universe 快照ID：{{ plan.universe_snapshot_id }}</p>
          <p>账户快照ID：{{ plan.account_snapshot_id ?? '-' }}</p>
          <p>台账最近更新：<FormatValues kind="time" :value="plan.ledger_max_updated_at" /></p>
          <div v-if="planSymbols.length" class="flex flex-wrap gap-2">
            <RouterLink
              v-for="symbol in planSymbols"
              :key="symbol"
              class="text-primary underline"
              :to="{ path: '/market', query: { symbol } }"
              :aria-label="`返回 ${symbol} 行情（计划 ${plan.plan_id}）`"
            >{{ symbol }} 行情</RouterLink>
          </div>
        </template>
        <p v-else-if="requestedPlanId && requestedPlanQuery.error.value" class="text-muted-foreground">指定计划数据不可用</p>
        <p v-else-if="!requestedPlanId && planQuery.error.value" class="text-muted-foreground">计划数据不可用</p>
        <p v-else-if="requestedPlanId" class="text-muted-foreground">正在定位指定计划</p>
        <p v-else class="text-muted-foreground">暂无计划快照引用</p>
      </div>
    </section>
  </div>
</template>
