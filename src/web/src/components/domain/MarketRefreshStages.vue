<script setup lang="ts">
import { computed } from 'vue'
import Badge from '@/components/ui/Badge.vue'
import type {
  MarketRefreshProgressStatus,
  MarketRefreshStageProgress,
} from '@/composables/useMarketRefreshCoordinator'

interface Props {
  stages: Partial<Record<'backfill' | 'intraday', MarketRefreshStageProgress>>
}

const props = defineProps<Props>()

const rows = computed(() => [
  { key: 'backfill' as const, label: '日 K 回填', stage: props.stages.backfill },
  { key: 'intraday' as const, label: '报价与分时', stage: props.stages.intraday },
].filter((row) => row.stage !== undefined))

const statusConfig: Record<MarketRefreshProgressStatus, {
  label: string
  variant: 'success' | 'warning' | 'danger' | 'neutral'
}> = {
  running: { label: '运行中', variant: 'neutral' },
  succeeded: { label: '已完成', variant: 'success' },
  degraded: { label: '已降级', variant: 'warning' },
  failed: { label: '失败', variant: 'danger' },
}

function modeText(mode: MarketRefreshStageProgress['mode']): string {
  if (mode === 'display_only') return '展示模式'
  if (mode === 'decision') return '决策模式'
  return '-'
}
</script>

<template>
  <section
    v-if="rows.length"
    class="overflow-x-auto border-y border-border"
    aria-label="行情刷新阶段详情"
  >
    <table class="w-full min-w-[760px] table-fixed text-sm">
      <thead class="text-left text-xs text-muted-foreground">
        <tr>
          <th class="w-28 py-2 pr-3">阶段</th>
          <th class="w-24 py-2 pr-3">状态</th>
          <th class="w-64 py-2 pr-3">运行 ID</th>
          <th class="w-32 py-2 pr-3">执行</th>
          <th class="py-2">告警</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.key"
          class="border-t border-border align-top"
        >
          <td class="py-2 pr-3 font-medium">{{ row.label }}</td>
          <td class="py-2 pr-3">
            <Badge :variant="statusConfig[row.stage!.status].variant">
              {{ statusConfig[row.stage!.status].label }}
            </Badge>
          </td>
          <td class="break-all py-2 pr-3 font-mono text-xs">{{ row.stage!.runId }}</td>
          <td class="py-2 pr-3 text-xs">
            <div>{{ row.stage!.reused ? '复用已有运行' : '新运行' }}</div>
            <div class="text-muted-foreground">{{ modeText(row.stage!.mode) }}</div>
          </td>
          <td class="py-2 text-xs">
            <ul v-if="row.stage!.warnings.length" class="space-y-1">
              <li
                v-for="warning in row.stage!.warnings"
                :key="warning"
                class="break-words"
              >
                {{ warning }}
              </li>
            </ul>
            <span v-else class="text-muted-foreground">-</span>
          </td>
        </tr>
      </tbody>
    </table>
  </section>
</template>
