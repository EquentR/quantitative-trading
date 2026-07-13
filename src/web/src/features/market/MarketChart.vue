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
  <div ref="element" class="market-chart" role="img" :aria-label="label" />
</template>
