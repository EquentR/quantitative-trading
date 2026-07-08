<script setup lang="ts">
import { ClipboardList } from 'lucide-vue-next'
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold">复盘</h1>
    <div class="flex items-start gap-2 text-sm text-muted-foreground">
      <ClipboardList class="mt-0.5 size-4 shrink-0" />
      <p>推荐记录、人工执行反馈、审计日志将在后续 API 落地后接入</p>
    </div>
    <p class="text-xs text-muted-foreground">当前页面不展示假建议、假反馈或收益表现。</p>
  </div>
</template>
