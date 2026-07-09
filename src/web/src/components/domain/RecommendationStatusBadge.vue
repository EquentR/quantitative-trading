<script setup lang="ts">
import { computed } from 'vue'
import Badge from '@/components/ui/Badge.vue'
import type {
  RecommendationAction,
  RecommendationConfidence,
  NotificationProcessingStatus,
} from '@/api/types'

interface Props {
  kind: 'action' | 'confidence' | 'status'
  value: RecommendationAction | RecommendationConfidence | NotificationProcessingStatus
}

const props = defineProps<Props>()

type Variant = 'success' | 'warning' | 'danger' | 'neutral'

const actionMap: Record<RecommendationAction, { label: string; variant: Variant }> = {
  buy: { label: '买入', variant: 'warning' },
  sell: { label: '卖出', variant: 'danger' },
  add: { label: '加仓', variant: 'warning' },
  reduce: { label: '减仓', variant: 'warning' },
  hold: { label: '持有', variant: 'neutral' },
  watch: { label: '观察', variant: 'success' },
  avoid: { label: '不建议', variant: 'neutral' },
}

const confidenceMap: Record<RecommendationConfidence, { label: string; variant: Variant }> = {
  low: { label: '低', variant: 'neutral' },
  medium: { label: '中', variant: 'warning' },
  high: { label: '高', variant: 'success' },
}

const statusMap: Record<NotificationProcessingStatus, { label: string; variant: Variant }> = {
  unread: { label: '未读', variant: 'warning' },
  read: { label: '已读', variant: 'neutral' },
  feedback_recorded: { label: '已记录反馈', variant: 'success' },
}

const config = computed(() => {
  if (props.kind === 'action') return actionMap[props.value as RecommendationAction]
  if (props.kind === 'confidence') return confidenceMap[props.value as RecommendationConfidence]
  return statusMap[props.value as NotificationProcessingStatus]
})
</script>

<template>
  <Badge :variant="config?.variant ?? 'neutral'">{{ config?.label ?? '未知' }}</Badge>
</template>
