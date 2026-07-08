<script setup lang="ts">
import { computed } from 'vue'

interface Props {
  variant?: 'success' | 'warning' | 'danger' | 'neutral'
}

const props = withDefaults(defineProps<Props>(), { variant: 'neutral' })

const variantClass = computed(
  () =>
    ({
      success: 'bg-emerald-100 text-emerald-700',
      warning: 'bg-amber-100 text-amber-800',
      danger: 'bg-red-100 text-red-700',
      neutral: 'bg-muted text-muted-foreground',
    })[props.variant],
)
</script>

<template>
  <span class="inline-flex items-center rounded-sm px-2 py-0.5 text-xs font-medium" :class="variantClass">
    <slot />
  </span>
</template>
