<script setup lang="ts">
import { computed } from 'vue'
import Badge from '@/components/ui/Badge.vue'
import type { AccountSnapshotStatus, AuthStatus } from '@/api/types'

interface Props {
  authStatus?: AuthStatus | null
  schedulerRunning?: boolean | null
  snapshotStatus?: AccountSnapshotStatus | null
}

const props = defineProps<Props>()

const auth = computed(() => {
  if (!props.authStatus) return null
  return props.authStatus === 'configured'
    ? { label: '已配置', variant: 'success' as const }
    : { label: '待设置', variant: 'warning' as const }
})

const scheduler = computed(() =>
  props.schedulerRunning
    ? { label: '运行中', variant: 'success' as const }
    : { label: '未运行', variant: 'neutral' as const },
)

const snapshot = computed(() => {
  if (!props.snapshotStatus) return null
  if (props.snapshotStatus === 'ok') return { label: '完整', variant: 'success' as const }
  if (props.snapshotStatus === 'partial') return { label: '部分可用', variant: 'warning' as const }
  return { label: '不可用', variant: 'danger' as const }
})
</script>

<template>
  <div class="flex flex-wrap items-center gap-2">
    <Badge v-if="auth" :variant="auth.variant">认证 {{ auth.label }}</Badge>
    <Badge :variant="scheduler.variant">调度 {{ scheduler.label }}</Badge>
    <Badge v-if="snapshot" :variant="snapshot.variant">快照 {{ snapshot.label }}</Badge>
  </div>
</template>
