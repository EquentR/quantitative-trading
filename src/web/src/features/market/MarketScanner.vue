<script setup lang="ts">
import { computed, ref } from 'vue'
import Badge from '@/components/ui/Badge.vue'
import type { MarketSymbolSummary, RecommendationAction, UniverseSource } from '@/api/types'

const props = defineProps<{
  symbols: MarketSymbolSummary[]
  selectedSymbol: string | null
}>()

const emit = defineEmits<{
  select: [symbol: string]
}>()

const actionFilter = ref<'all' | RecommendationAction>('all')
const sourceFilter = ref<'all' | UniverseSource>('all')
const anomaliesOnly = ref(false)
const sortMode = ref<'recommendation' | 'change_desc' | 'symbol'>('recommendation')

const actions: RecommendationAction[] = ['buy', 'sell', 'add', 'reduce', 'hold', 'watch', 'avoid']
const actionPriority: Record<RecommendationAction, number> = {
  sell: 0,
  reduce: 1,
  buy: 2,
  add: 3,
  hold: 4,
  watch: 5,
  avoid: 6,
}

const filteredSymbols = computed(() => props.symbols
  .map((item, index) => ({ item, index }))
  .filter(({ item }) => {
    if (actionFilter.value !== 'all' && item.recommendation_action !== actionFilter.value) return false
    if (sourceFilter.value !== 'all' && !item.sources.includes(sourceFilter.value)) return false
    if (anomaliesOnly.value) {
      const healthy = item.quality_status === 'complete' || item.quality_status === 'ok'
      if (healthy && item.unread_count === 0 && item.warnings.length === 0) return false
    }
    return true
  })
  .sort((left, right) => {
    let comparison = 0
    if (sortMode.value === 'recommendation') {
      comparison = (left.item.recommendation_action === null ? 7 : actionPriority[left.item.recommendation_action])
        - (right.item.recommendation_action === null ? 7 : actionPriority[right.item.recommendation_action])
    } else if (sortMode.value === 'change_desc') {
      comparison = (right.item.change_pct ?? Number.NEGATIVE_INFINITY)
        - (left.item.change_pct ?? Number.NEGATIVE_INFINITY)
    } else {
      comparison = left.item.symbol.localeCompare(right.item.symbol)
    }
    return comparison || left.index - right.index
  })
  .map(({ item }) => item))

function sourceText(sources: UniverseSource[]): string {
  return sources.map((source) => source === 'holding' ? '持仓' : '自选').join(' / ')
}

function qualityText(status: MarketSymbolSummary['quality_status']): string {
  if (status === 'complete' || status === 'ok') return '完整'
  if (status === 'stale') return '过期'
  if (status === 'failed' || status === 'unavailable') return '不可用'
  return '部分可用'
}

function qualityVariant(status: MarketSymbolSummary['quality_status']) {
  if (status === 'complete' || status === 'ok') return 'success' as const
  if (status === 'failed' || status === 'unavailable') return 'danger' as const
  return 'warning' as const
}

function formatPrice(value: number | null): string {
  return value === null ? '不可用' : value.toLocaleString('zh-CN', { maximumFractionDigits: 3 })
}

function formatChange(value: number | null): string {
  return value === null ? '不可用' : `${value.toFixed(2)}%`
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col" aria-label="决策标的筛选">
    <div class="space-y-2 border-b border-border p-3">
      <label class="block text-xs font-medium">
        排序方式
        <select
          v-model="sortMode"
          class="mt-1 block w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        >
          <option value="recommendation">建议动作优先</option>
          <option value="change_desc">涨跌幅从高到低</option>
          <option value="symbol">代码升序</option>
        </select>
      </label>
      <label class="block text-xs font-medium">
        动作筛选
        <select
          v-model="actionFilter"
          class="mt-1 block w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        >
          <option value="all">全部动作</option>
          <option v-for="action in actions" :key="action" :value="action">{{ action }}</option>
        </select>
      </label>
      <label class="block text-xs font-medium">
        来源筛选
        <select
          v-model="sourceFilter"
          class="mt-1 block w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        >
          <option value="all">全部来源</option>
          <option value="holding">持仓</option>
          <option value="watch_pinned">自选置顶</option>
        </select>
      </label>
      <label class="flex items-center gap-2 text-xs">
        <input v-model="anomaliesOnly" type="checkbox" class="size-4" />
        仅看异常或未读
      </label>
    </div>

    <div class="min-h-0 space-y-1 overflow-y-auto p-2">
      <button
        v-for="item in filteredSymbols"
        :key="item.symbol"
        type="button"
        class="w-full rounded-md border px-2.5 py-2 text-left text-xs hover:bg-muted"
        :class="item.symbol === selectedSymbol ? 'border-primary bg-blue-50' : 'border-border bg-background'"
        :aria-pressed="item.symbol === selectedSymbol"
        @click="emit('select', item.symbol)"
      >
        <span class="flex min-w-0 items-start justify-between gap-2">
          <span class="min-w-0 break-words font-medium">{{ item.symbol }} {{ item.name }}</span>
          <span v-if="item.unread_count" class="shrink-0 text-amber-700">未读 {{ item.unread_count }}</span>
        </span>
        <span class="mt-1 grid grid-cols-2 gap-x-2 gap-y-1 text-muted-foreground">
          <span>{{ sourceText(item.sources) }}</span>
          <span class="text-right">{{ formatPrice(item.current_price) }} / {{ formatChange(item.change_pct) }}</span>
          <span>{{ item.recommendation_action ?? '无建议' }} / {{ item.intraday_strength }}</span>
          <span class="text-right">计划 {{ item.plan_status ?? '不可用' }}</span>
        </span>
        <span class="mt-1.5 flex flex-wrap gap-1">
          <Badge :variant="qualityVariant(item.quality_status)">{{ qualityText(item.quality_status) }}</Badge>
        </span>
      </button>
      <p v-if="filteredSymbols.length === 0" class="px-2 py-4 text-center text-xs text-muted-foreground">
        当前筛选条件下没有标的
      </p>
    </div>
  </div>
</template>
