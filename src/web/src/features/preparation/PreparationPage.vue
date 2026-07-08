<script setup lang="ts">
import PositionLedgerPanel from './PositionLedgerPanel.vue'
import CashAccountPanel from './CashAccountPanel.vue'
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold">准备</h1>
    <p class="text-sm text-muted-foreground">
      交易日前维护台账和资金账户，所有写操作只改变本地手动记录。
    </p>
    <PositionLedgerPanel />
    <CashAccountPanel />
  </div>
</template>
