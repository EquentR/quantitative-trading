<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { BarChart, CandlestickChart, LineChart } from 'echarts/charts'
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from 'echarts/components'
import { init, use, type EChartsCoreOption, type EChartsType } from 'echarts/core'
import { SVGRenderer } from 'echarts/renderers'

use([
  BarChart,
  CandlestickChart,
  LineChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  SVGRenderer,
])

const props = defineProps<{
  label: string
  option: EChartsCoreOption
  qualityMarker?: string | null
}>()

const element = ref<HTMLElement | null>(null)
let chart: EChartsType | null = null

function resize() {
  chart?.resize()
}

onMounted(() => {
  if (!element.value) return
  chart = init(element.value, undefined, { renderer: 'svg' })
  chart.setOption(props.option, true)
  window.addEventListener('resize', resize)
})

watch(
  () => props.option,
  (option) => chart?.setOption(option, true),
  { deep: true },
)

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div class="market-chart relative" role="img" :aria-label="label">
    <div ref="element" class="size-full" />
    <span
      v-if="qualityMarker"
      class="pointer-events-none absolute right-2 top-10 z-10 max-w-[calc(100%-1rem)] break-words rounded-sm border border-amber-400 bg-amber-50/95 px-2 py-1 text-xs font-medium text-amber-900 shadow-sm"
      data-testid="chart-quality-marker"
    >
      {{ qualityMarker }}
    </span>
  </div>
</template>
