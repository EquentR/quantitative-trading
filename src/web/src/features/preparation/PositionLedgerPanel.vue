<script setup lang="ts">
import { ref } from 'vue'
import { Download, FileJson, FileUp, Pencil, Plus, Trash2 } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import {
  downloadPositionsCsv,
  useDeletePositionMutation,
  useImportPositionsCsvMutation,
  useImportPositionsMutation,
  usePositionsQuery,
  useSavePositionMutation,
} from '@/queries/positions'
import type { Position, PositionInput } from '@/api/types'

const positionsQuery = usePositionsQuery()
const saveMutation = useSavePositionMutation()
const deleteMutation = useDeletePositionMutation()
const importMutation = useImportPositionsMutation()
const importCsvMutation = useImportPositionsCsvMutation()

const showForm = ref(false)
const showJsonImport = ref(false)
const jsonImportText = ref('')
const csvInput = ref<HTMLInputElement | null>(null)
const editingSymbol = ref<string | null>(null)
const form = ref<PositionInput>({
  symbol: '',
  name: '',
  quantity: 0,
  available_quantity: 0,
  cost_price: 0,
  opened_at: '',
  note: '',
})

async function onSave() {
  await saveMutation.mutateAsync({
    mode: editingSymbol.value ? 'update' : 'create',
    position: { ...form.value },
  })
  showForm.value = false
  resetForm()
}

function resetForm() {
  editingSymbol.value = null
  form.value = { symbol: '', name: '', quantity: 0, available_quantity: 0, cost_price: 0, opened_at: '', note: '' }
}

function onEdit(position: Position) {
  editingSymbol.value = position.symbol
  form.value = {
    symbol: position.symbol,
    name: position.name,
    quantity: position.quantity,
    available_quantity: position.available_quantity,
    cost_price: position.cost_price,
    opened_at: position.opened_at,
    note: position.note,
  }
  showForm.value = true
}

async function onDelete(symbol: string) {
  if (!window.confirm('删除台账记录不代表真实卖出或撤单')) return
  await deleteMutation.mutateAsync(symbol)
}

async function onJsonImport() {
  const parsed = JSON.parse(jsonImportText.value) as PositionInput[] | { positions: PositionInput[] }
  const positions = Array.isArray(parsed) ? parsed : parsed.positions
  await importMutation.mutateAsync(positions)
  jsonImportText.value = ''
  showJsonImport.value = false
}

async function onCsvSelected(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  await importCsvMutation.mutateAsync(file)
  if (csvInput.value) csvInput.value.value = ''
}

async function onExportCsv() {
  const blob = await downloadPositionsCsv()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'positions.csv'
  link.click()
  URL.revokeObjectURL(url)
}
</script>

<template>
  <section class="space-y-3">
    <div class="flex items-center justify-between">
      <h3 class="text-sm font-medium">手动持仓台账</h3>
      <Button variant="primary" @click="showForm = !showForm">
        <Plus class="size-4" />
        新增台账记录
      </Button>
    </div>

    <p class="text-xs text-muted-foreground">删除台账记录不代表真实卖出或撤单</p>

    <div class="flex flex-wrap gap-2">
      <Button variant="secondary" @click="showJsonImport = !showJsonImport">
        <FileJson class="size-4" />
        导入 JSON 台账
      </Button>
      <Button variant="secondary" @click="csvInput?.click()">
        <FileUp class="size-4" />
        导入 CSV 台账
      </Button>
      <Button variant="secondary" @click="onExportCsv">
        <Download class="size-4" />
        导出 CSV 台账
      </Button>
      <input ref="csvInput" class="sr-only" type="file" accept=".csv,text/csv" @change="onCsvSelected" />
    </div>

    <form v-if="showJsonImport" class="space-y-2 rounded-md border border-border p-3" @submit.prevent="onJsonImport">
      <label class="block">
        <span class="text-xs font-medium">JSON 台账内容</span>
        <textarea
          v-model="jsonImportText"
          class="mt-1 min-h-24 w-full rounded-md border border-border px-2 py-1 text-sm"
          placeholder='[{"symbol":"600000","name":"示例银行","quantity":1000,...}]'
        />
      </label>
      <Button type="submit" variant="primary" :loading="importMutation.isPending.value">
        保存导入台账
      </Button>
    </form>

    <table v-if="positionsQuery.data.value?.length" class="w-full text-sm">
      <thead class="text-left text-xs text-muted-foreground">
        <tr>
          <th class="py-1">代码</th>
          <th class="py-1">名称</th>
          <th class="py-1">数量</th>
          <th class="py-1">可用</th>
          <th class="py-1">成本价</th>
          <th class="py-1 text-right">维护</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="p in positionsQuery.data.value" :key="p.symbol" class="border-t border-border">
          <td class="py-1">{{ p.symbol }}</td>
          <td class="py-1">{{ p.name }}</td>
          <td class="py-1">{{ p.quantity }}</td>
          <td class="py-1">{{ p.available_quantity }}</td>
          <td class="py-1"><FormatValues kind="money" :value="p.cost_price" /></td>
          <td class="py-1">
            <div class="flex justify-end gap-1">
              <Button variant="ghost" @click="onEdit(p)">
                <Pencil class="size-4" />
                编辑
              </Button>
              <Button variant="danger" :loading="deleteMutation.isPending.value" @click="onDelete(p.symbol)">
                <Trash2 class="size-4" />
                删除台账记录
              </Button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="text-sm text-muted-foreground">暂无台账记录。</p>

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
          <span class="text-xs font-medium">持仓数量</span>
          <input v-model.number="form.quantity" type="number" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">可用数量</span>
          <input v-model.number="form.available_quantity" type="number" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">成本价</span>
          <input v-model.number="form.cost_price" type="number" step="0.01" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
        <label class="block">
          <span class="text-xs font-medium">建仓日期</span>
          <input v-model="form.opened_at" type="date" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
        </label>
      </div>
      <label class="block">
        <span class="text-xs font-medium">备注</span>
        <input v-model="form.note" class="mt-1 block w-full rounded-md border border-border px-2 py-1 text-sm" />
      </label>
      <Button type="submit" variant="primary" :loading="saveMutation.isPending.value">
        保存到手动台账
      </Button>
    </form>
  </section>
</template>
