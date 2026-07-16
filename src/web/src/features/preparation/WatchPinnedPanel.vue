<script setup lang="ts">
import { ref } from 'vue'
import { Download, FileJson, FileUp, ListPlus, Pencil, Plus, Search, Trash2 } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Alert from '@/components/ui/Alert.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import { ApiError } from '@/api/client'
import InstrumentCandidatePanel from './InstrumentCandidatePanel.vue'
import {
  useWatchlistPinnedQuery,
  useCreatePinnedItemMutation,
  useUpdatePinnedItemMutation,
  useDeletePinnedItemMutation,
  useImportPinnedItemsMutation,
  useImportPinnedCsvMutation,
  useWatchlistExportCsv,
} from '@/queries/watchlist'
import type { WatchPinnedItem, WatchPinnedInput } from '@/api/types'

const pinnedQuery = useWatchlistPinnedQuery()
const createMutation = useCreatePinnedItemMutation()
const updateMutation = useUpdatePinnedItemMutation()
const deleteMutation = useDeletePinnedItemMutation()
const importMutation = useImportPinnedItemsMutation()
const importCsvMutation = useImportPinnedCsvMutation()

const showForm = ref(false)
const candidateMode = ref<'eastmoney' | 'search' | null>(null)
const showJsonImport = ref(false)
const jsonImportText = ref('')
const importError = ref('')
const importWarnings = ref<string[]>([])
const csvInput = ref<HTMLInputElement | null>(null)
const editingSymbol = ref<string | null>(null)
const form = ref<WatchPinnedInput>({
  symbol: '',
  name: '',
  rank: 0,
  plan_enabled: false,
  note: '',
})

function resetForm() {
  editingSymbol.value = null
  form.value = { symbol: '', name: '', rank: 0, plan_enabled: false, note: '' }
}

function importErrorText(error: unknown) {
  if (error instanceof ApiError && error.code === 'validation_error') {
    return '导入失败：导入内容不符合要求，请检查代码、排序和字段格式'
  }
  if (error instanceof ApiError && error.message) return `导入失败：${error.message}`
  return '导入失败，请稍后重试'
}

function onAdd() {
  resetForm()
  showForm.value = true
}

function onEdit(item: WatchPinnedItem) {
  editingSymbol.value = item.symbol
  form.value = {
    symbol: item.symbol,
    name: item.name,
    rank: item.rank,
    plan_enabled: item.plan_enabled,
    note: item.note,
  }
  showForm.value = true
}

async function onSave() {
  if (editingSymbol.value) {
    await updateMutation.mutateAsync({ symbol: editingSymbol.value, input: { ...form.value } })
  } else {
    await createMutation.mutateAsync({ ...form.value })
  }
  showForm.value = false
  resetForm()
}

async function onToggle(item: WatchPinnedItem) {
  await updateMutation.mutateAsync({
    symbol: item.symbol,
    input: {
      symbol: item.symbol,
      name: item.name,
      rank: item.rank,
      plan_enabled: !item.plan_enabled,
      note: item.note,
    },
  })
}

async function onDelete(item: WatchPinnedItem) {
  if (!window.confirm('删除自选记录仅删除本地观察记录，不代表真实持仓或交易')) return
  await deleteMutation.mutateAsync(item.symbol)
}

async function onJsonImport() {
  importError.value = ''
  importWarnings.value = []

  let parsed: unknown
  try {
    parsed = JSON.parse(jsonImportText.value)
  } catch {
    importError.value = 'JSON 格式错误，请检查输入内容'
    return
  }

  let items: WatchPinnedInput[]
  if (Array.isArray(parsed)) {
    items = parsed as WatchPinnedInput[]
  } else if (
    parsed &&
    typeof parsed === 'object' &&
    Array.isArray((parsed as { items?: unknown }).items)
  ) {
    items = (parsed as { items: WatchPinnedInput[] }).items
  } else {
    importError.value = '不支持的信封格式，请使用数组或 { "items": [...] }'
    return
  }

  try {
    const result = await importMutation.mutateAsync(items)
    importWarnings.value = result.warnings ?? []
    jsonImportText.value = ''
    showJsonImport.value = false
  } catch (error) {
    importError.value = importErrorText(error)
  }
}

async function onCsvSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importError.value = ''
  importWarnings.value = []
  try {
    const result = await importCsvMutation.mutateAsync(file)
    importWarnings.value = result.warnings ?? []
  } catch (error) {
    importError.value = importErrorText(error)
  } finally {
    input.value = ''
  }
}

async function onExportCsv() {
  const blob = await useWatchlistExportCsv()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'watchlist-pinned.csv'
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <section class="space-y-3">
    <div class="flex items-center justify-between">
      <h3 class="text-sm font-medium">自选置顶观察池</h3>
      <Button variant="primary" @click="onAdd">
        <Plus class="size-4" />
        新增自选记录
      </Button>
    </div>

    <p class="text-xs text-muted-foreground">删除自选记录仅删除本地观察记录，不代表真实持仓或交易</p>

    <div class="flex flex-wrap gap-2">
      <Button variant="secondary" @click="candidateMode = 'eastmoney'">
        <ListPlus class="size-4" />
        从东方财富选择
      </Button>
      <Button variant="secondary" @click="candidateMode = 'search'">
        <Search class="size-4" />
        按名称或代码搜索
      </Button>
      <Button variant="secondary" @click="showJsonImport = !showJsonImport">
        <FileJson class="size-4" />
        导入自选观察项
      </Button>
      <Button variant="secondary" @click="csvInput?.click()">
        <FileUp class="size-4" />
        导入 CSV 自选
      </Button>
      <Button variant="secondary" @click="onExportCsv">
        <Download class="size-4" />
        导出 CSV 自选
      </Button>
      <input ref="csvInput" class="sr-only" type="file" accept=".csv,text/csv" aria-label="CSV 文件输入" @change="onCsvSelected" />
    </div>

    <p class="text-xs font-medium text-amber-700">JSON/CSV 导入会全量替换当前观察池</p>

    <Alert v-if="importError" variant="danger">
      {{ importError }}
    </Alert>

    <Alert v-if="importWarnings.length" variant="warning">
      <span v-for="warning in importWarnings" :key="warning" class="block">{{ warning }}</span>
    </Alert>

    <InstrumentCandidatePanel
      v-if="candidateMode"
      :key="candidateMode"
      :load-eastmoney-on-mount="candidateMode === 'eastmoney'"
      @close="candidateMode = null"
    />

    <form v-if="showJsonImport" class="space-y-2 rounded-md border border-border p-3" @submit.prevent="onJsonImport">
      <label class="block">
        <span class="text-xs font-medium">自选 JSON 内容</span>
        <textarea
          v-model="jsonImportText"
          class="mt-1 min-h-24 w-full rounded-md border border-border px-2 py-1 text-sm"
          placeholder='[{"symbol":"600519","name":"示例白酒","rank":1,"plan_enabled":true,"note":"核心自选"}]'
        />
      </label>
      <Button type="submit" variant="primary" :loading="importMutation.isPending.value">
        保存导入自选
      </Button>
    </form>

    <table v-if="pinnedQuery.data.value?.length" class="w-full text-sm">
      <thead class="text-left text-xs text-muted-foreground">
        <tr>
          <th class="py-1">排序</th>
          <th class="py-1">代码/名称</th>
          <th class="py-1">计划</th>
          <th class="py-1">来源</th>
          <th class="py-1">备注</th>
          <th class="py-1">更新时间</th>
          <th class="py-1 text-right">维护</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in pinnedQuery.data.value" :key="item.symbol" class="border-t border-border">
          <td class="py-1">{{ item.rank }}</td>
          <td class="py-1">
            <div>{{ item.symbol }}</div>
            <div class="text-xs text-muted-foreground">{{ item.name }}</div>
          </td>
          <td class="py-1">
            <input
              type="checkbox"
              class="size-4"
              :checked="item.plan_enabled"
              :aria-label="`计划启用 ${item.symbol}`"
              :disabled="updateMutation.isPending.value"
              @change="onToggle(item)"
            />
          </td>
          <td class="py-1">{{ item.source }}</td>
          <td class="py-1">{{ item.note }}</td>
          <td class="py-1"><FormatValues kind="time" :value="item.updated_at" /></td>
          <td class="py-1">
            <div class="flex justify-end gap-1">
              <Button variant="ghost" @click="onEdit(item)">
                <Pencil class="size-4" />
                编辑
              </Button>
              <Button variant="danger" :loading="deleteMutation.isPending.value" @click="onDelete(item)">
                <Trash2 class="size-4" />
                删除本地自选记录
              </Button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="text-sm text-muted-foreground">暂无自选记录。</p>

    <form v-if="showForm" class="space-y-3 rounded-md border border-border p-4" @submit.prevent="onSave">
      <div class="grid grid-cols-2 gap-3">
        <label class="block">
          <span class="text-xs font-medium">股票代码</span>
          <input v-model="form.symbol" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">股票名称</span>
          <input v-model="form.name" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">排序权重</span>
          <input v-model.number="form.rank" type="number" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">计划启用</span>
          <input v-model="form.plan_enabled" type="checkbox" class="mt-1 size-4" />
        </label>
      </div>
      <label class="block">
        <span class="text-xs font-medium">备注</span>
        <input v-model="form.note" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
      </label>
      <Button type="submit" variant="primary" :loading="createMutation.isPending.value || updateMutation.isPending.value">
        保存自选观察项
      </Button>
    </form>
  </section>
</template>
