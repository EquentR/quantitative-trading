<script setup lang="ts">
import { computed, ref } from 'vue'
import Button from '@/components/ui/Button.vue'
import Alert from '@/components/ui/Alert.vue'
import Badge from '@/components/ui/Badge.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import {
  useDatasourceStatusQuery,
  useUpdateDatasourceKeyMutation,
  useDeleteDatasourceKeyMutation,
  useCheckDatasourceMutation,
} from '@/queries/datasource'
import { ApiError } from '@/api/client'

const statusQuery = useDatasourceStatusQuery()
const updateMutation = useUpdateDatasourceKeyMutation()
const deleteMutation = useDeleteDatasourceKeyMutation()
const checkMutation = useCheckDatasourceMutation()

// API key lives only in this local ref; never persisted to Pinia or localStorage.
const apiKey = ref('')
const submitError = ref('')

const badge = computed(() => {
  const status = statusQuery.data.value?.status
  if (status === 'configured') return { text: '已配置', variant: 'success' as const }
  if (status === 'invalid') return { text: '无效', variant: 'danger' as const }
  return { text: '未配置', variant: 'warning' as const }
})

const isMissing = computed(() => statusQuery.data.value?.status === 'missing')

async function onSubmitKey() {
  if (!apiKey.value) return
  submitError.value = ''
  try {
    await updateMutation.mutateAsync({ api_key: apiKey.value })
    apiKey.value = ''
  } catch (err) {
    // Show a generic or non-secret backend error message; never echo the key.
    if (err instanceof ApiError && err.message) {
      submitError.value = `保存失败：${err.message}`
    } else {
      submitError.value = '保存失败，请稍后重试'
    }
    // Keep the typed key so the user can retry.
  }
}

async function onResetKey() {
  if (!window.confirm('重置仅清除本地行情数据源凭证，不影响账户持仓。确认继续？')) return
  await deleteMutation.mutateAsync()
}

async function onCheck() {
  await checkMutation.mutateAsync()
}
</script>

<template>
  <section class="space-y-3">
    <h3 class="text-sm font-medium">数据源设置</h3>
    <p class="text-xs text-muted-foreground">仅维护行情数据源凭证，不涉及账户密码</p>

    <Alert v-if="isMissing" variant="warning">
      行情数据源尚未配置，请提交 API Key。
    </Alert>

    <Alert v-if="submitError" variant="danger">
      {{ submitError }}
    </Alert>

    <div class="space-y-1 text-sm">
      <p>数据源：<span class="font-medium">东方财富/妙想</span></p>
      <p>
        状态：<Badge :variant="badge.variant">{{ badge.text }}</Badge>
      </p>
      <p v-if="statusQuery.data.value?.last_checked_at">
        最近检查：<FormatValues kind="time" :value="statusQuery.data.value.last_checked_at" />
      </p>
      <p v-if="statusQuery.data.value?.last_error" class="text-xs text-muted-foreground">
        错误：{{ statusQuery.data.value.last_error }}
      </p>
    </div>

    <form class="space-y-3 rounded-md border border-border p-4" @submit.prevent="onSubmitKey">
      <label class="block">
        <span class="text-xs font-medium">API Key</span>
        <input
          v-model="apiKey"
          type="password"
          autocomplete="off"
          class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm"
          placeholder="输入行情数据源 API Key"
        />
      </label>
      <div class="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" :loading="updateMutation.isPending.value">
          保存 API Key
        </Button>
        <Button variant="secondary" :loading="deleteMutation.isPending.value" @click="onResetKey">
          重置 API Key
        </Button>
        <Button variant="secondary" :loading="checkMutation.isPending.value" @click="onCheck">
          检查连接
        </Button>
      </div>
    </form>
  </section>
</template>
