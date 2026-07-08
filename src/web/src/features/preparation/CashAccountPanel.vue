<script setup lang="ts">
import { computed, ref } from 'vue'
import Button from '@/components/ui/Button.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import Alert from '@/components/ui/Alert.vue'
import { ApiError } from '@/api/client'
import {
  useCashAccountQuery,
  useCashAdjustmentMutation,
  useCashTransactionsQuery,
  useCashTransferMutation,
  useInitializeCashAccountMutation,
} from '@/queries/cash'

const cashQuery = useCashAccountQuery()
const transactionsQuery = useCashTransactionsQuery()
const initializeMutation = useInitializeCashAccountMutation()
const transferMutation = useCashTransferMutation()
const adjustmentMutation = useCashAdjustmentMutation()

const cash = ref({ amount: 0, note: '' })
const initForm = ref({ cash: 0, note: 'initial principal' })
const adjustmentForm = ref({ cash: 0, note: '' })
const showInitialize = ref(false)
const showAdjustment = ref(false)

const cashNotInitialized = computed(() => {
  const error = cashQuery.error.value
  return error instanceof ApiError && error.code === 'cash_account_not_initialized'
})

async function onInitialize() {
  await initializeMutation.mutateAsync({ ...initForm.value })
  showInitialize.value = false
  initForm.value = { cash: 0, note: 'initial principal' }
}

async function onTransfer(type: 'transfer_in' | 'transfer_out') {
  await transferMutation.mutateAsync({ type, amount: cash.value.amount, note: cash.value.note })
  cash.value = { amount: 0, note: '' }
}

async function onAdjustCash() {
  if (!window.confirm('现金校准只修改手动资金账户，不代表券商资金变化。确认继续？')) return
  await adjustmentMutation.mutateAsync({ ...adjustmentForm.value })
  showAdjustment.value = false
  adjustmentForm.value = { cash: 0, note: '' }
}
</script>

<template>
  <section class="space-y-3">
    <h3 class="text-sm font-medium">手动资金账户</h3>
    <p class="text-xs text-muted-foreground">不代表券商资金变化</p>

    <Alert v-if="cashNotInitialized" variant="warning">
      手动资金账户尚未初始化，请先记录初始本金。
    </Alert>

    <div class="flex flex-wrap gap-2">
      <Button variant="secondary" @click="showInitialize = !showInitialize">
        初始化资金账户
      </Button>
      <Button variant="secondary" @click="showAdjustment = !showAdjustment">
        现金校准
      </Button>
    </div>

    <form v-if="showInitialize" class="space-y-3 rounded-md border border-border p-4" @submit.prevent="onInitialize">
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label class="block">
          <span class="text-xs font-medium">初始现金</span>
          <input v-model.number="initForm.cash" type="number" step="0.01" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">备注</span>
          <input v-model="initForm.note" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
      </div>
      <Button type="submit" variant="primary" :loading="initializeMutation.isPending.value">
        保存手动资金账户
      </Button>
    </form>

    <form v-if="showAdjustment" class="space-y-3 rounded-md border border-border p-4" @submit.prevent="onAdjustCash">
      <p class="text-xs text-muted-foreground">现金校准用于修正手动资金账户，不代表券商资金变化，备注必填。</p>
      <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
        <label class="block">
          <span class="text-xs font-medium">校准后现金</span>
          <input v-model.number="adjustmentForm.cash" type="number" step="0.01" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">校准备注</span>
          <input v-model="adjustmentForm.note" required class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
      </div>
      <Button type="submit" variant="primary" :loading="adjustmentMutation.isPending.value">
        保存现金校准
      </Button>
    </form>

    <div class="space-y-1 text-sm" v-if="cashQuery.data.value">
      <p>现金余额：<FormatValues kind="money" :value="cashQuery.data.value.cash_balance" /></p>
      <p>净本金：<FormatValues kind="money" :value="cashQuery.data.value.net_principal" /></p>
      <p>更新时间：<FormatValues kind="time" :value="cashQuery.data.value.updated_at" /></p>
    </div>

    <form class="space-y-3 rounded-md border border-border p-4" @submit.prevent>
      <div class="grid grid-cols-2 gap-3">
        <label class="block">
          <span class="text-xs font-medium">金额</span>
          <input v-model.number="cash.amount" type="number" step="0.01" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">备注</span>
          <input v-model="cash.note" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
      </div>
      <div class="flex gap-2">
        <Button variant="primary" :loading="transferMutation.isPending.value" @click="onTransfer('transfer_in')">
          记录模拟银证转入
        </Button>
        <Button variant="secondary" :loading="transferMutation.isPending.value" @click="onTransfer('transfer_out')">
          记录模拟银证转出
        </Button>
      </div>
    </form>

    <section class="space-y-2">
      <h4 class="text-sm font-medium">资金流水</h4>
      <ul v-if="transactionsQuery.data.value?.length" class="space-y-1 text-sm">
        <li v-for="item in transactionsQuery.data.value" :key="item.id ?? item.occurred_at" class="border-t border-border pt-1">
          {{ item.type }} <FormatValues kind="money" :value="item.amount" /> {{ item.note }}
        </li>
      </ul>
      <p v-else class="text-sm text-muted-foreground">暂无资金流水。</p>
    </section>
  </section>
</template>
