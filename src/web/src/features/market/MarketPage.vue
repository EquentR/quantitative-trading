<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { RefreshCw, SlidersHorizontal, X } from 'lucide-vue-next'
import type { EChartsCoreOption } from 'echarts/core'
import Alert from '@/components/ui/Alert.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import RecommendationStatusBadge from '@/components/domain/RecommendationStatusBadge.vue'
import type { MarketQualityStatus, MarketStrengthComponent } from '@/api/types'
import {
  useDailyBarsQuery,
  useIntradayStrengthQuery,
  useMarketOverviewQuery,
  useMarketSymbolsQuery,
  useMarketTraceQuery,
  useMinuteBarsQuery,
  useMoneyFlowQuery,
} from '@/queries/market'
import MarketChart from './MarketChart.vue'
import MarketScanner from './MarketScanner.vue'

type MarketTab = 'overview' | 'daily' | 'money' | 'intraday' | 'trace'

const route = useRoute()
const router = useRouter()

const tabs: { value: MarketTab; label: string }[] = [
  { value: 'overview', label: '概览' },
  { value: 'daily', label: 'K 线' },
  { value: 'money', label: '资金流' },
  { value: 'intraday', label: '分时强弱' },
  { value: 'trace', label: '数据引用' },
]

const activeTab = ref<MarketTab>('overview')
const selectedSymbol = ref<string | null>(null)
const drawerOpen = ref(false)
const dailyWindow = ref(250)
const moneyWindow = ref(60)

const symbolsQuery = useMarketSymbolsQuery()
const symbols = computed(() => symbolsQuery.data.value?.items ?? [])
const requestedSymbol = computed(() => {
  const value = Array.isArray(route.query.symbol) ? route.query.symbol[0] : route.query.symbol
  return typeof value === 'string' && /^\d{6}$/.test(value) ? value : null
})

watch([symbols, requestedSymbol], ([items, requested]) => {
  if (items.length === 0) {
    selectedSymbol.value = null
    return
  }
  if (requested && items.some((item) => item.symbol === requested)) {
    selectedSymbol.value = requested
    return
  }
  if (!items.some((item) => item.symbol === selectedSymbol.value)) {
    selectedSymbol.value = items[0].symbol
  }
}, { immediate: true })

const selectedSummary = computed(() =>
  symbols.value.find((item) => item.symbol === selectedSymbol.value) ?? null,
)

const overviewQuery = useMarketOverviewQuery(selectedSymbol)
const dailyQuery = useDailyBarsQuery(selectedSymbol)
const moneyQuery = useMoneyFlowQuery(selectedSymbol)
const minuteQuery = useMinuteBarsQuery(selectedSymbol)
const strengthQuery = useIntradayStrengthQuery(selectedSymbol)
const snapshotId = computed(() => overviewQuery.data.value?.snapshot_id ?? null)
const traceQuery = useMarketTraceQuery(snapshotId, selectedSymbol)

const overview = computed(() => overviewQuery.data.value)
const displayName = computed(() => overview.value?.name ?? selectedSummary.value?.name ?? '')
const combinedRisks = computed(() => Array.from(new Set([
  ...(overview.value?.plan?.invalid_if ?? []),
  ...(overview.value?.risks ?? []),
])))

function selectSymbol(symbol: string) {
  selectedSymbol.value = symbol
  drawerOpen.value = false
  void router.replace({ query: { ...route.query, symbol } })
}

function qualityText(status: MarketQualityStatus): string {
  if (status === 'complete' || status === 'ok') return '数据完整'
  if (status === 'partial' || status === 'degraded') return '数据部分可用'
  if (status === 'stale') return '数据已过期'
  return '数据不可用'
}

function qualityVariant(status: MarketQualityStatus) {
  if (status === 'complete' || status === 'ok') return 'success' as const
  if (status === 'failed' || status === 'unavailable') return 'danger' as const
  return 'warning' as const
}

function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '不可用'
  return value.toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '不可用'
  return `${value.toFixed(2)}%`
}

function componentDirection(component: MarketStrengthComponent): string {
  if (component.direction === 1) return '偏强'
  if (component.direction === -1) return '偏弱'
  return '中性'
}

function staleChartMarker(status: MarketQualityStatus | undefined, dataTime: string | null | undefined): string | null {
  if (status !== 'stale') return null
  return `陈旧数据 · ${dataTime ?? '数据时间不可用'}`
}

const dailyBars = computed(() => (dailyQuery.data.value?.bars ?? []).slice(-dailyWindow.value))
const dailyOption = computed<EChartsCoreOption>(() => ({
  animation: false,
  tooltip: { trigger: 'axis' },
  legend: { top: 0, data: ['日 K', 'MA5', 'MA10', 'MA20', 'MA60', '成交量'] },
  grid: [
    { left: 52, right: 24, top: 46, height: '56%' },
    { left: 52, right: 24, top: '76%', height: '14%' },
  ],
  xAxis: [
    { type: 'category', data: dailyBars.value.map((bar) => bar.trade_date), boundaryGap: true },
    { type: 'category', gridIndex: 1, data: dailyBars.value.map((bar) => bar.trade_date), axisLabel: { show: false }, boundaryGap: true },
  ],
  yAxis: [
    { scale: true, splitLine: { lineStyle: { color: '#e5e7eb' } } },
    { gridIndex: 1, splitNumber: 2, axisLabel: { formatter: (value: number) => `${Math.round(value / 10000)}万` } },
  ],
  dataZoom: [
    { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
    { type: 'slider', xAxisIndex: [0, 1], bottom: 4, height: 18, start: 0, end: 100 },
  ],
  series: [
    {
      name: '日 K',
      type: 'candlestick',
      data: dailyBars.value.map((bar) => [bar.open, bar.close, bar.low, bar.high]),
      itemStyle: { color: '#dc2626', color0: '#059669', borderColor: '#dc2626', borderColor0: '#059669' },
    },
    ...(['ma5', 'ma10', 'ma20', 'ma60'] as const).map((key, index) => ({
      name: key.toUpperCase(),
      type: 'line' as const,
      data: dailyBars.value.map((bar) => bar[key]),
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 1.25, color: ['#2563eb', '#d97706', '#7c3aed', '#475569'][index] },
    })),
    {
      name: '成交量',
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: dailyBars.value.map((bar) => bar.volume),
      itemStyle: { color: '#94a3b8' },
    },
  ],
}))

const moneyRows = computed(() => (moneyQuery.data.value?.rows ?? []).slice(-moneyWindow.value))
const moneyOption = computed<EChartsCoreOption>(() => ({
  animation: false,
  tooltip: { trigger: 'axis' },
  legend: { top: 0, type: 'scroll' },
  grid: { left: 64, right: 54, top: 56, bottom: 56 },
  xAxis: { type: 'category', data: moneyRows.value.map((row) => row.trade_date) },
  yAxis: [
    { type: 'value', name: '净额', axisLabel: { formatter: (value: number) => `${Math.round(value / 10000)}万` } },
    { type: 'value', name: '占比', axisLabel: { formatter: (value: number) => `${value.toFixed(0)}%` } },
  ],
  dataZoom: [
    { type: 'inside', start: 0, end: 100 },
    { type: 'slider', bottom: 8, height: 18, start: 0, end: 100 },
  ],
  series: [
    { name: '主力净额', type: 'bar', data: moneyRows.value.map((row) => row.main_net_amount), itemStyle: { color: '#2563eb' } },
    { name: '主力净占比', type: 'line', yAxisIndex: 1, data: moneyRows.value.map((row) => row.main_net_ratio), showSymbol: false, lineStyle: { color: '#dc2626' } },
    { name: '超大单净额', type: 'line', data: moneyRows.value.map((row) => row.super_large_net_amount), showSymbol: false, lineStyle: { color: '#7c3aed' } },
    { name: '超大单占比', type: 'line', yAxisIndex: 1, data: moneyRows.value.map((row) => row.super_large_net_ratio), showSymbol: false, lineStyle: { color: '#7c3aed', type: 'dashed' } },
    { name: '大单净额', type: 'line', data: moneyRows.value.map((row) => row.large_net_amount), showSymbol: false, lineStyle: { color: '#0891b2' } },
    { name: '大单占比', type: 'line', yAxisIndex: 1, data: moneyRows.value.map((row) => row.large_net_ratio), showSymbol: false, lineStyle: { color: '#0891b2', type: 'dashed' } },
    { name: '中单净额', type: 'line', data: moneyRows.value.map((row) => row.medium_net_amount), showSymbol: false, lineStyle: { color: '#d97706' } },
    { name: '中单占比', type: 'line', yAxisIndex: 1, data: moneyRows.value.map((row) => row.medium_net_ratio), showSymbol: false, lineStyle: { color: '#d97706', type: 'dashed' } },
    { name: '小单净额', type: 'line', data: moneyRows.value.map((row) => row.small_net_amount), showSymbol: false, lineStyle: { color: '#64748b' } },
    { name: '小单占比', type: 'line', yAxisIndex: 1, data: moneyRows.value.map((row) => row.small_net_ratio), showSymbol: false, lineStyle: { color: '#64748b', type: 'dashed' } },
  ],
}))

const minuteBars = computed(() => minuteQuery.data.value?.bars ?? [])
const minuteMarkers = computed(() => minuteQuery.data.value?.recommendation_markers ?? [])
const intradayOption = computed<EChartsCoreOption>(() => ({
  animation: false,
  tooltip: { trigger: 'axis' },
  legend: { top: 0, data: ['价格', '前收', 'VWAP', '分钟成交量'] },
  grid: [
    { left: 52, right: 24, top: 46, height: '57%' },
    { left: 52, right: 24, top: '77%', height: '13%' },
  ],
  xAxis: [
    { type: 'category', data: minuteBars.value.map((bar) => bar.minute), boundaryGap: false },
    { type: 'category', gridIndex: 1, data: minuteBars.value.map((bar) => bar.minute), axisLabel: { show: false } },
  ],
  yAxis: [
    { type: 'value', scale: true, splitLine: { lineStyle: { color: '#e5e7eb' } } },
    { type: 'value', gridIndex: 1, splitNumber: 2, axisLabel: { formatter: (value: number) => `${Math.round(value / 10000)}万` } },
  ],
  dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 }],
  series: [
    {
      name: '价格',
      type: 'line',
      data: minuteBars.value.map((bar) => bar.close),
      showSymbol: false,
      lineStyle: { color: '#2563eb', width: 2 },
      markPoint: {
        symbolSize: 42,
        data: minuteMarkers.value.map((marker) => ({
          name: `${marker.time} ${marker.action}`,
          coord: [marker.time, marker.price],
          value: marker.action,
        })),
      },
    },
    {
      name: '前收',
      type: 'line',
      data: minuteBars.value.map(() => minuteQuery.data.value?.previous_close ?? null),
      showSymbol: false,
      lineStyle: { type: 'dashed', color: '#64748b' },
    },
    {
      name: 'VWAP',
      type: 'line',
      data: minuteBars.value.map((bar) => bar.vwap),
      showSymbol: false,
      connectNulls: false,
      lineStyle: { color: '#d97706' },
    },
    {
      name: '分钟成交量',
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: minuteBars.value.map((bar) => bar.volume),
      itemStyle: { color: '#94a3b8' },
    },
  ],
}))
</script>

<template>
  <div class="space-y-3">
    <div class="flex flex-wrap items-center gap-2">
      <h1 class="text-lg font-semibold">行情</h1>
      <Button class="market-mobile-trigger ml-auto" aria-label="选择决策标的" @click="drawerOpen = true">
        <SlidersHorizontal class="size-4" />
        选择决策标的
      </Button>
    </div>

    <p class="text-xs text-muted-foreground">决策股票池来自手动持仓台账和后端启用的自选置顶标的。</p>

    <div v-if="symbolsQuery.isPending.value" class="border-y border-border py-8 text-center text-sm text-muted-foreground">
      正在加载决策标的
    </div>
    <Alert v-else-if="symbolsQuery.error.value" variant="danger">
      <div class="flex flex-wrap items-center gap-2">
        <span>决策标的加载失败</span>
        <Button aria-label="重试加载标的" @click="symbolsQuery.refetch()">
          <RefreshCw class="size-4" />
          重试加载标的
        </Button>
      </div>
    </Alert>
    <div v-else-if="symbols.length === 0" class="border-y border-border py-8 text-center text-sm text-muted-foreground">
      当前没有决策启用标的
    </div>

    <div v-else class="market-workbench min-w-0 border-y border-border">
      <aside class="market-scanner-desktop min-h-[38rem] border-r border-border" aria-label="决策标的扫描器">
        <MarketScanner :symbols="symbols" :selected-symbol="selectedSymbol" @select="selectSymbol" />
      </aside>

      <section class="min-w-0 p-3 md:p-4" aria-label="当前标的详情">
        <div class="flex min-w-0 flex-wrap items-end justify-between gap-2">
          <div class="min-w-0">
            <h2 class="break-words text-base font-semibold">{{ displayName }}</h2>
            <p class="text-xs text-muted-foreground">{{ selectedSymbol }}</p>
          </div>
          <p v-if="selectedSummary" class="text-xs text-muted-foreground">
            行情时间：<FormatValues kind="time" :value="selectedSummary.data_time" />
          </p>
        </div>

        <div class="mt-3 overflow-x-auto border-b border-border" role="tablist" aria-label="行情详情">
          <div class="flex min-w-max gap-1">
            <button
              v-for="tab in tabs"
              :key="tab.value"
              type="button"
              role="tab"
              class="border-b-2 px-3 py-2 text-sm"
              :class="activeTab === tab.value ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'"
              :aria-selected="activeTab === tab.value"
              @click="activeTab = tab.value"
            >
              {{ tab.label }}
            </button>
          </div>
        </div>

        <div v-if="activeTab === 'overview'" class="space-y-4 pt-4" role="tabpanel">
          <p v-if="overviewQuery.isPending.value" class="text-sm text-muted-foreground">正在加载标的概览</p>
          <Alert v-else-if="overviewQuery.error.value" variant="danger">
            <div class="flex flex-wrap items-center gap-2">
              <span>标的概览加载失败</span>
              <Button @click="overviewQuery.refetch()"><RefreshCw class="size-4" />重试</Button>
            </div>
          </Alert>
          <template v-else-if="overview">
            <Alert
              v-if="overview.status !== 'complete' && overview.status !== 'ok'"
              :variant="overview.status === 'failed' || overview.status === 'unavailable' ? 'danger' : 'warning'"
            >
              <div class="space-y-1">
                <p class="font-medium">{{ qualityText(overview.status) }}</p>
                <p>数据时间：<FormatValues kind="time" :value="overview.data_time" /></p>
                <ul v-if="overview.warnings.length" class="list-disc pl-4">
                  <li v-for="warning in overview.warnings" :key="warning" class="break-words">{{ warning }}</li>
                </ul>
              </div>
            </Alert>

            <div class="grid gap-4 lg:grid-cols-2">
              <section class="space-y-2 border-b border-border pb-3">
                <h3 class="text-sm font-medium">持仓与活动计划</h3>
                <div v-if="overview.position" class="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <span class="text-muted-foreground">持仓 / 可用</span><span>{{ overview.position.quantity }} / {{ overview.position.available_quantity }}</span>
                  <span class="text-muted-foreground">成本价</span><span>{{ formatNumber(overview.position.cost_price, 3) }}</span>
                  <span class="text-muted-foreground">浮动盈亏</span><span>{{ formatPercent(overview.position.floating_pnl_pct) }}</span>
                </div>
                <p v-else class="text-sm text-muted-foreground">非持仓标的</p>
                <div v-if="overview.plan" class="space-y-1 text-sm">
                  <p>计划：<RouterLink class="text-primary underline" :to="{ path: '/review', query: { plan_id: overview.plan.plan_id } }">{{ overview.plan.plan_id }}</RouterLink></p>
                  <p>状态：{{ overview.plan.status }}</p>
                  <p class="break-words">允许动作：{{ overview.plan.allowed_actions.join(' / ') || '不可用' }}</p>
                  <p>有效期：<FormatValues kind="time" :value="overview.plan.valid_until" /></p>
                </div>
                <p v-else class="text-sm text-muted-foreground">当前没有活动计划</p>
              </section>

              <section class="space-y-2 border-b border-border pb-3">
                <h3 class="text-sm font-medium">最新建议</h3>
                <template v-if="overview.recommendation">
                  <div class="flex flex-wrap gap-2">
                    <RecommendationStatusBadge kind="action" :value="overview.recommendation.action" />
                    <RecommendationStatusBadge kind="confidence" :value="overview.recommendation.confidence" />
                  </div>
                  <RouterLink class="block break-all text-sm text-primary underline" :to="{ path: '/recommendations', query: { recommendation_id: overview.recommendation.recommendation_id } }">
                    {{ overview.recommendation.recommendation_id }}
                  </RouterLink>
                  <ul class="list-disc space-y-1 pl-4 text-sm">
                    <li v-for="reason in overview.recommendation.reason" :key="reason" class="break-words">{{ reason }}</li>
                  </ul>
                  <p class="text-xs text-muted-foreground">建议数据时间：<FormatValues kind="time" :value="overview.recommendation.data_time" /></p>
                </template>
                <p v-else class="text-sm text-muted-foreground">当前没有建议</p>
              </section>

              <section class="space-y-2 border-b border-border pb-3">
                <h3 class="text-sm font-medium">市场结构</h3>
                <template v-if="overview.market_structure">
                  <dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                    <dt class="text-muted-foreground">趋势</dt><dd>{{ overview.market_structure.trend }}</dd>
                    <dt class="text-muted-foreground">支撑</dt><dd>{{ formatNumber(overview.market_structure.support, 3) }}</dd>
                    <dt class="text-muted-foreground">阻力</dt><dd>{{ formatNumber(overview.market_structure.resistance, 3) }}</dd>
                    <dt class="text-muted-foreground">ATR14</dt><dd>{{ formatNumber(overview.market_structure.atr14, 3) }}</dd>
                  </dl>
                  <p class="break-words text-sm">{{ overview.market_structure.reason }}</p>
                </template>
                <p v-else class="text-sm text-muted-foreground">市场结构不可用</p>
              </section>

              <section class="space-y-2 border-b border-border pb-3">
                <h3 class="text-sm font-medium">分时强弱组件</h3>
                <template v-if="overview.intraday_strength">
                  <p class="text-sm">{{ overview.intraday_strength.label }} / {{ overview.intraday_strength.confidence }}</p>
                  <p v-if="overview.intraday_strength.degraded_reason" class="break-words text-sm text-amber-700">
                    {{ overview.intraday_strength.degraded_reason }}
                  </p>
                  <ul class="space-y-1 text-sm">
                    <li v-for="component in overview.intraday_strength.components" :key="component.key" class="break-words">
                      <span class="font-medium">{{ component.label }}</span>：{{ componentDirection(component) }}，{{ component.reason }}
                    </li>
                  </ul>
                </template>
                <p v-else class="text-sm text-muted-foreground">分时强弱不可用</p>
              </section>
            </div>

            <section class="space-y-2">
              <h3 class="text-sm font-medium">风险与失效条件</h3>
              <ul v-if="combinedRisks.length" class="list-disc space-y-1 pl-4 text-sm">
                <li v-for="risk in combinedRisks" :key="risk" class="break-words">{{ risk }}</li>
              </ul>
              <p v-else class="text-sm text-muted-foreground">后端未返回风险说明</p>
            </section>
          </template>
        </div>

        <div v-else-if="activeTab === 'daily'" class="space-y-3 pt-4" role="tabpanel">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 class="text-sm font-medium">前复权日 K 线</h3>
              <p class="text-xs text-muted-foreground">前复权</p>
            </div>
            <label class="text-xs">时间窗口
              <select v-model="dailyWindow" aria-label="K 线时间窗口" class="ml-1 rounded-md border border-border bg-background px-2 py-1">
                <option :value="60">60 日</option><option :value="120">120 日</option><option :value="250">250 日</option>
              </select>
            </label>
          </div>
          <p v-if="dailyQuery.isPending.value" class="text-sm text-muted-foreground">正在加载日 K 数据</p>
          <Alert v-else-if="dailyQuery.error.value" variant="danger">日 K 数据加载失败</Alert>
          <p v-else-if="dailyBars.length === 0" class="text-sm text-muted-foreground">暂无日 K 数据</p>
          <template v-else>
            <Alert v-if="dailyQuery.data.value!.status !== 'complete' && dailyQuery.data.value!.status !== 'ok'" variant="warning">
              {{ qualityText(dailyQuery.data.value!.status) }}
              <span v-for="warning in dailyQuery.data.value!.warnings" :key="warning" class="ml-1 break-words">{{ warning }}</span>
            </Alert>
            <MarketChart
              label="前复权日 K 线、均线与成交量图"
              :option="dailyOption"
              :quality-marker="staleChartMarker(dailyQuery.data.value?.status, dailyQuery.data.value?.data_time)"
            />
          </template>
        </div>

        <div v-else-if="activeTab === 'money'" class="space-y-3 pt-4" role="tabpanel">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3 class="text-sm font-medium">资金流拆分</h3>
            <label class="text-xs">时间窗口
              <select v-model="moneyWindow" aria-label="资金流时间窗口" class="ml-1 rounded-md border border-border bg-background px-2 py-1">
                <option :value="20">20 日</option><option :value="60">60 日</option>
              </select>
            </label>
          </div>
          <p v-if="moneyQuery.isPending.value" class="text-sm text-muted-foreground">正在加载资金流数据</p>
          <Alert v-else-if="moneyQuery.error.value" variant="danger">资金流数据加载失败</Alert>
          <p v-else-if="moneyRows.length === 0" class="text-sm text-muted-foreground">暂无资金流数据</p>
          <template v-else>
            <Alert v-if="moneyQuery.data.value!.status !== 'complete' && moneyQuery.data.value!.status !== 'ok'" variant="warning">
              <span>{{ qualityText(moneyQuery.data.value!.status) }}</span>
              <span v-for="warning in moneyQuery.data.value!.warnings" :key="warning" class="ml-1 break-words">{{ warning }}</span>
            </Alert>
            <MarketChart
              label="资金流净额与占比图"
              :option="moneyOption"
              :quality-marker="staleChartMarker(moneyQuery.data.value?.status, moneyQuery.data.value?.data_time)"
            />
            <div class="table-scroll">
              <table class="w-full table-fixed text-xs md:min-w-[80rem]" aria-label="资金流完整明细">
                <thead class="text-left text-muted-foreground"><tr>
                  <th class="py-1">日期</th><th>主力净额</th><th>主力净占比</th><th>超大单净额</th><th>超大单占比</th><th>大单净额</th><th>大单占比</th><th>中单净额</th><th>中单占比</th><th>小单净额</th><th>小单占比</th>
                </tr></thead>
                <tbody><tr v-for="row in moneyQuery.data.value!.rows" :key="row.trade_date" class="border-t border-border">
                  <td class="py-1.5">{{ row.trade_date }}</td><td>{{ formatNumber(row.main_net_amount, 0) }}</td><td>{{ formatPercent(row.main_net_ratio) }}</td>
                  <td>{{ formatNumber(row.super_large_net_amount, 0) }}</td><td>{{ formatPercent(row.super_large_net_ratio) }}</td>
                  <td>{{ formatNumber(row.large_net_amount, 0) }}</td><td>{{ formatPercent(row.large_net_ratio) }}</td>
                  <td>{{ formatNumber(row.medium_net_amount, 0) }}</td><td>{{ formatPercent(row.medium_net_ratio) }}</td>
                  <td>{{ formatNumber(row.small_net_amount, 0) }}</td><td>{{ formatPercent(row.small_net_ratio) }}</td>
                </tr></tbody>
              </table>
            </div>
          </template>
        </div>

        <div v-else-if="activeTab === 'intraday'" class="space-y-3 pt-4" role="tabpanel">
          <h3 class="text-sm font-medium">当日分时强弱</h3>
          <p v-if="minuteQuery.isPending.value || strengthQuery.isPending.value" class="text-sm text-muted-foreground">正在加载分时数据</p>
          <Alert v-else-if="minuteQuery.error.value" variant="danger">分钟行情加载失败</Alert>
          <Alert v-else-if="strengthQuery.error.value" variant="warning">强弱组件加载失败，分钟行情仍可查看</Alert>
          <p v-else-if="minuteBars.length === 0" class="text-sm text-muted-foreground">暂无当日分钟行情</p>
          <template v-else>
            <Alert v-if="minuteQuery.data.value!.status !== 'complete' && minuteQuery.data.value!.status !== 'ok'" variant="warning">
              {{ qualityText(minuteQuery.data.value!.status) }}
            </Alert>
            <MarketChart
              label="分时价格、VWAP 与成交量图"
              :option="intradayOption"
              :quality-marker="staleChartMarker(minuteQuery.data.value?.status, minuteQuery.data.value?.data_time)"
            />
            <div class="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
              <span>前收 {{ formatNumber(minuteQuery.data.value!.previous_close, 3) }}</span>
              <span><span>VWAP</span> 使用后端分钟数据</span>
              <span>行情时间 <FormatValues kind="time" :value="minuteQuery.data.value!.data_time" /></span>
            </div>
            <ul v-if="minuteMarkers.length" class="space-y-1 text-sm">
              <li v-for="marker in minuteMarkers" :key="marker.recommendation_id" class="break-words">
                <span>建议发生点 {{ marker.time }} {{ marker.action }}</span>
                <RouterLink class="ml-1 text-primary underline" :to="{ path: '/recommendations', query: { recommendation_id: marker.recommendation_id } }">{{ marker.recommendation_id }}</RouterLink>
              </li>
            </ul>
            <section v-if="strengthQuery.data.value" class="space-y-2 border-t border-border pt-3">
              <div class="flex flex-wrap gap-2 text-sm">
                <Badge :variant="qualityVariant(strengthQuery.data.value.status)">{{ strengthQuery.data.value.label }}</Badge>
                <span>置信度 {{ strengthQuery.data.value.confidence }}</span>
                <span>规则 {{ strengthQuery.data.value.rule_version }}</span>
              </div>
              <p v-if="strengthQuery.data.value.degraded_reason" class="break-words text-sm text-amber-700">{{ strengthQuery.data.value.degraded_reason }}</p>
              <table class="w-full table-fixed text-xs" aria-label="分时强弱组件">
                <thead class="text-left text-muted-foreground"><tr><th class="w-1/4 py-1">组件</th><th class="w-1/6">方向</th><th>后端理由</th></tr></thead>
                <tbody><tr v-for="component in strengthQuery.data.value.components" :key="component.key" class="border-t border-border align-top">
                  <td class="py-1.5 break-words">{{ component.label }}</td><td>{{ componentDirection(component) }}</td><td class="break-words">{{ component.reason }}</td>
                </tr></tbody>
              </table>
            </section>
          </template>
        </div>

        <div v-else class="space-y-3 pt-4" role="tabpanel">
          <h3 class="text-sm font-medium">决策数据引用</h3>
          <p v-if="snapshotId === null && overviewQuery.isPending.value" class="text-sm text-muted-foreground">正在解析输入快照</p>
          <Alert v-else-if="traceQuery.error.value" variant="danger">数据引用加载失败</Alert>
          <p v-else-if="snapshotId === null" class="text-sm text-muted-foreground">当前概览没有输入快照引用</p>
          <template v-else-if="traceQuery.data.value">
            <Alert v-if="traceQuery.data.value.status !== 'complete' && traceQuery.data.value.status !== 'ok'" variant="warning">
              <span>{{ qualityText(traceQuery.data.value.status) }}</span>
              <span v-for="warning in traceQuery.data.value.warnings" :key="warning" class="ml-1 break-words">{{ warning }}</span>
            </Alert>
            <dl class="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[10rem_minmax(0,1fr)]">
              <dt class="text-muted-foreground">run_id</dt><dd class="break-all">{{ traceQuery.data.value.run_id }}</dd>
              <dt class="text-muted-foreground">snapshot_id</dt><dd class="break-all">{{ traceQuery.data.value.snapshot_id }}</dd>
              <dt class="text-muted-foreground">plan_id</dt><dd class="break-all">{{ traceQuery.data.value.plan_id ?? '不可用' }}</dd>
              <dt class="text-muted-foreground">recommendation_id</dt><dd class="break-all">{{ traceQuery.data.value.recommendation_id ?? '不可用' }}</dd>
              <dt class="text-muted-foreground">数据时间</dt><dd><FormatValues kind="time" :value="traceQuery.data.value.data_time" /></dd>
              <dt class="text-muted-foreground">Stale 阈值</dt><dd>{{ traceQuery.data.value.thresholds.stale_trading_minutes ?? '不可用' }} 个有效交易分钟</dd>
            </dl>
            <div class="table-scroll">
              <table class="w-full table-fixed text-xs md:min-w-[48rem]" aria-label="数据集引用">
                <thead class="text-left text-muted-foreground"><tr><th>数据集</th><th>引用</th><th>来源</th><th>质量</th><th>数据时间</th><th>Warnings</th></tr></thead>
                <tbody><tr v-for="dataset in traceQuery.data.value.datasets" :key="dataset.dataset" class="border-t border-border align-top">
                  <td class="py-1.5 break-words">{{ dataset.dataset }}</td><td class="break-all">{{ dataset.reference_id ?? '不可用' }}</td>
                  <td class="break-words">{{ dataset.source }}</td><td>{{ qualityText(dataset.status) }}</td>
                  <td><FormatValues kind="time" :value="dataset.data_time" /></td><td class="break-words">{{ dataset.warnings.join(' / ') || '-' }}</td>
                </tr></tbody>
              </table>
            </div>
          </template>
          <p v-else class="text-sm text-muted-foreground">正在加载数据引用</p>
        </div>
      </section>
    </div>

    <div v-if="drawerOpen" class="fixed inset-0 z-50 bg-black/30 md:hidden" @click.self="drawerOpen = false">
      <section class="h-full w-[min(22rem,92vw)] bg-background shadow-xl" role="dialog" aria-modal="true" aria-label="决策标的扫描器">
        <div class="flex items-center justify-between border-b border-border px-3 py-2">
          <h2 class="text-sm font-medium">决策标的扫描器</h2>
          <button type="button" class="rounded-md p-1 hover:bg-muted" aria-label="关闭决策标的扫描器" @click="drawerOpen = false">
            <X class="size-5" />
          </button>
        </div>
        <div class="h-[calc(100%-2.75rem)]">
          <MarketScanner :symbols="symbols" :selected-symbol="selectedSymbol" @select="selectSymbol" />
        </div>
      </section>
    </div>
  </div>
</template>
