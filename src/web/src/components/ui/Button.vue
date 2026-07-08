<script setup lang="ts">
import { computed } from 'vue'
import { Loader2 } from 'lucide-vue-next'

interface Props {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
  loading?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  variant: 'secondary',
  type: 'button',
  disabled: false,
  loading: false,
})

const variantClass = computed(
  () =>
    ({
      primary: 'bg-primary text-primary-foreground hover:opacity-90',
      secondary: 'bg-background text-foreground border border-border hover:bg-muted',
      danger: 'bg-danger text-white hover:opacity-90',
      ghost: 'bg-transparent text-foreground hover:bg-muted',
    })[props.variant],
)
</script>

<template>
  <button
    :type="type"
    :disabled="disabled || loading"
    class="inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-50"
    :class="variantClass"
  >
    <Loader2 v-if="loading" class="size-4 animate-spin" />
    <slot />
  </button>
</template>
