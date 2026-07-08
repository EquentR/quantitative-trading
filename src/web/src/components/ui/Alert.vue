<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle, Info, XCircle } from 'lucide-vue-next'

interface Props {
  variant?: 'warning' | 'danger' | 'neutral'
}

const props = withDefaults(defineProps<Props>(), { variant: 'neutral' })

const config = computed(
  () =>
    ({
      warning: { icon: AlertTriangle, cls: 'border-amber-300 bg-amber-50 text-amber-800' },
      danger: { icon: XCircle, cls: 'border-red-300 bg-red-50 text-red-800' },
      neutral: { icon: Info, cls: 'border-border bg-muted text-muted-foreground' },
    })[props.variant],
)
</script>

<template>
  <div class="flex items-start gap-2 rounded-md border px-3 py-2 text-sm" :class="config.cls">
    <component :is="config.icon" class="mt-0.5 size-4 shrink-0" />
    <div><slot /></div>
  </div>
</template>
