<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  kind: 'money' | 'ratio' | 'time'
  value: number | string | null | undefined
}

const props = defineProps<Props>()

const text = computed(() => {
  const v = props.value
  if (v === null || v === undefined || v === '') return '不可用'

  if (props.kind === 'money') {
    const n = Number(v)
    if (!Number.isFinite(n)) return '不可用'
    return '¥' + n.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 })
  }

  if (props.kind === 'ratio') {
    const n = Number(v)
    if (!Number.isFinite(n)) return '不可用'
    return (n * 100).toFixed(2) + '%'
  }

  const d = new Date(v as string)
  if (Number.isNaN(d.getTime())) return '不可用'
  return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
})
</script>

<template>
  <span>{{ text }}</span>
</template>
