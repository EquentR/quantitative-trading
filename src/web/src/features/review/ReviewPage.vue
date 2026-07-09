<script setup lang="ts">
import { computed } from 'vue'
import Alert from '@/components/ui/Alert.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import RecommendationStatusBadge from '@/components/domain/RecommendationStatusBadge.vue'
import { useRecommendationsQuery } from '@/queries/recommendations'
import { useNotificationsQuery } from '@/queries/notifications'
import { useLatestPlanQuery } from '@/queries/plans'
import AuditLogPanel from './AuditLogPanel.vue'
import FeedbackPanel from './FeedbackPanel.vue'
import type { Recommendation } from '@/api/types'

const recommendationsQuery = useRecommendationsQuery()
const notificationsQuery = useNotificationsQuery()
const planQuery = useLatestPlanQuery()

const recommendations = computed(() => recommendationsQuery.data.value ?? [])
const notifications = computed(() => notificationsQuery.data.value ?? [])
const plan = computed(() => planQuery.data.value)
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
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold">复盘</h1>
    <p class="text-sm text-muted-foreground">复盘用于决策回顾与反馈记录，不自动真实下单，不代表收益保证。</p>

    <section class="space-y-2">
      <h2 class="text-sm font-medium">推荐记录</h2>
      <Alert v-if="recommendationsQuery.error.value" variant="warning">
        <p>推荐数据不可用</p>
      </Alert>
      <div v-if="recommendations.length" class="overflow-x-auto">
        <table class="w-full table-fixed text-xs">
          <thead class="text-left text-muted-foreground">
            <tr>
              <th class="w-1/5 py-1">股票</th>
              <th class="w-[8%] py-1">动作</th>
              <th class="w-[8%] py-1">置信度</th>
              <th class="w-1/4 py-1">关键价位</th>
              <th class="w-1/5 py-1">数据时间</th>
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
                <div>{{ r.symbol }}</div>
                <div class="text-muted-foreground">{{ r.name }}</div>
              </td>
              <td class="py-1.5"><RecommendationStatusBadge kind="action" :value="r.action" /></td>
              <td class="py-1.5"><RecommendationStatusBadge kind="confidence" :value="r.confidence" /></td>
              <td class="py-1.5 break-words whitespace-normal">{{ keyPriceText(r) }}</td>
              <td class="py-1.5"><FormatValues kind="time" :value="r.data_time" /></td>
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
        <table class="w-full table-fixed text-xs">
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
              <td class="py-1.5">{{ n.symbol }}</td>
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

    <section class="space-y-2">
      <h2 class="text-sm font-medium">输入快照引用</h2>
      <div class="space-y-1 text-sm">
        <template v-if="plan">
          <p>计划ID：{{ plan.plan_id }}</p>
          <p>Universe 快照ID：{{ plan.universe_snapshot_id }}</p>
          <p>账户快照ID：{{ plan.account_snapshot_id ?? '-' }}</p>
          <p>台账最近更新：<FormatValues kind="time" :value="plan.ledger_max_updated_at" /></p>
        </template>
        <p v-else-if="planQuery.error.value" class="text-muted-foreground">计划数据不可用</p>
        <p v-else class="text-muted-foreground">暂无计划快照引用</p>
      </div>
    </section>
  </div>
</template>
