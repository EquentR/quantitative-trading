<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ChevronDown, ChevronUp, RefreshCw, Search, X } from 'lucide-vue-next'
import Alert from '@/components/ui/Alert.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import { ApiError } from '@/api/client'
import type { InstrumentCandidate, InstrumentPreview } from '@/api/types'
import {
  useEastmoneyCandidatesMutation,
  useInstrumentSearchMutation,
} from '@/queries/instruments'
import { useSelectPinnedItemsMutation } from '@/queries/watchlist'

const emit = defineEmits<{ close: [] }>()
const props = withDefaults(defineProps<{ loadEastmoneyOnMount?: boolean }>(), {
  loadEastmoneyOnMount: false,
})

const eastmoneyMutation = useEastmoneyCandidatesMutation()
const searchMutation = useInstrumentSearchMutation()
const selectMutation = useSelectPinnedItemsMutation()
const query = ref('')
const preview = ref<InstrumentPreview | null>(null)
const selectedSymbols = ref<string[]>([])
const errorMessage = ref('')
const successMessage = ref('')
const resultWarnings = ref<string[]>([])
const previewWarningsOpen = ref(false)

const isLoading = computed(
  () => eastmoneyMutation.isPending.value || searchMutation.isPending.value,
)
const selectedCount = computed(() => selectedSymbols.value.length)
const previewWarnings = computed(() => [...new Set(preview.value?.warnings ?? [])])

function candidateType(candidate: InstrumentCandidate) {
  if (candidate.instrument_type === 'a_share') return 'A 股'
  if (candidate.instrument_type === 'etf') return 'ETF'
  return '未知'
}

function settlementLabel(candidate: InstrumentCandidate) {
  if (candidate.settlement_cycle === 't0') return 'T+0'
  if (candidate.settlement_cycle === 't1') return 'T+1'
  return '未知'
}

function sourceError(error: unknown) {
  if (!(error instanceof ApiError)) return '请求失败，请稍后重试'
  const messages: Record<string, string> = {
    datasource_not_configured: '东方财富数据源尚未配置，请先保存 API Key',
    datasource_invalid: '东方财富 API Key 无效，请重新配置',
    datasource_quota_exceeded: '东方财富调用额度已耗尽，请稍后再试',
    datasource_unavailable: '东方财富网络连接不可用，请稍后重试',
    datasource_contract_error: '东方财富响应格式已变化，暂时无法读取候选',
    instrument_directory_unavailable: '证券目录暂不可用，请稍后重试',
  }
  return messages[error.code] ?? error.message ?? '请求失败，请稍后重试'
}

function acceptPreview(value: InstrumentPreview) {
  preview.value = value
  previewWarningsOpen.value = false
  selectedSymbols.value = []
  resultWarnings.value = []
  errorMessage.value = ''
  successMessage.value = ''
}

async function loadEastmoney() {
  errorMessage.value = ''
  try {
    acceptPreview(await eastmoneyMutation.mutateAsync())
  } catch (error) {
    errorMessage.value = sourceError(error)
  }
}

async function searchInstruments() {
  const trimmed = query.value.trim()
  if (!trimmed) {
    errorMessage.value = '请输入股票名称或六位代码'
    return
  }
  errorMessage.value = ''
  try {
    acceptPreview(await searchMutation.mutateAsync(trimmed))
  } catch (error) {
    errorMessage.value = sourceError(error)
  }
}

function toggleCandidate(symbol: string, checked: boolean) {
  selectedSymbols.value = checked
    ? [...selectedSymbols.value, symbol]
    : selectedSymbols.value.filter((item) => item !== symbol)
}

async function selectCandidates() {
  if (!preview.value || selectedSymbols.value.length === 0) return
  errorMessage.value = ''
  try {
    const result = await selectMutation.mutateAsync({
      preview_id: preview.value.preview_id,
      symbols: preview.value.items
        .filter((item) => selectedSymbols.value.includes(item.symbol))
        .map((item) => item.symbol),
    })
    resultWarnings.value = result.warnings
    successMessage.value = `已加入 ${selectedSymbols.value.length} 个监控标的`
    preview.value = null
    selectedSymbols.value = []
  } catch (error) {
    if (error instanceof ApiError && error.code === 'instrument_preview_expired') {
      preview.value = null
      selectedSymbols.value = []
      errorMessage.value = '候选预览已过期，请重新获取'
      return
    }
    if (error instanceof ApiError && error.code === 'instrument_selection_invalid') {
      selectedSymbols.value = []
      errorMessage.value = '所选证券已失效，请重新选择'
      return
    }
    errorMessage.value = sourceError(error)
  }
}

onMounted(() => {
  if (props.loadEastmoneyOnMount) void loadEastmoney()
})
</script>

<template>
  <div class="space-y-3 border-y border-border py-3">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <h4 class="text-sm font-medium">选择监控证券</h4>
        <p class="text-xs text-muted-foreground">确认加入后默认启用计划，实际状态以后端返回为准。</p>
      </div>
      <Button variant="ghost" aria-label="关闭候选选择" @click="emit('close')">
        <X class="size-4" />
      </Button>
    </div>

    <div class="flex flex-wrap gap-2">
      <Button :loading="eastmoneyMutation.isPending.value" :disabled="isLoading" @click="loadEastmoney">
        <RefreshCw class="size-4" />
        刷新东方财富候选
      </Button>
      <form class="flex min-w-0 flex-1 gap-2" role="search" @submit.prevent="searchInstruments">
        <label class="min-w-40 flex-1">
          <span class="sr-only">股票名称或代码</span>
          <input
            v-model="query"
            type="search"
            class="h-9 w-full rounded-md border border-border px-2 text-sm"
            placeholder="股票名称或六位代码"
            aria-label="股票名称或代码"
          />
        </label>
        <Button type="submit" :loading="searchMutation.isPending.value" :disabled="isLoading">
          <Search class="size-4" />
          搜索证券
        </Button>
      </form>
    </div>

    <Alert v-if="errorMessage" variant="danger">{{ errorMessage }}</Alert>
    <Alert v-if="successMessage" variant="neutral">
      {{ successMessage }}
      <span v-for="warning in resultWarnings" :key="warning" class="block">{{ warning }}</span>
    </Alert>
    <Alert v-if="previewWarnings.length" variant="warning">
      <Button
        variant="ghost"
        class="w-full justify-between px-0"
        :aria-expanded="previewWarningsOpen"
        aria-controls="instrument-preview-warnings"
        @click="previewWarningsOpen = !previewWarningsOpen"
      >
        <span>目录校验提示 {{ previewWarnings.length }} 条</span>
        <ChevronUp v-if="previewWarningsOpen" class="size-4" />
        <ChevronDown v-else class="size-4" />
      </Button>
      <div
        v-if="previewWarningsOpen"
        id="instrument-preview-warnings"
        class="mt-2 max-h-40 space-y-1 overflow-y-auto border-t border-current/20 pt-2 text-xs"
      >
        <span v-for="warning in previewWarnings" :key="warning" class="block break-words">
          {{ warning }}
        </span>
      </div>
    </Alert>

    <p v-if="isLoading" class="text-sm text-muted-foreground" role="status">正在获取候选...</p>
    <p v-else-if="preview && preview.items.length === 0" class="text-sm text-muted-foreground">
      未找到可展示的候选证券。
    </p>

    <div v-if="preview?.items.length" class="table-scroll">
      <table class="w-full min-w-[46rem] table-fixed text-sm">
        <thead class="text-left text-xs text-muted-foreground">
          <tr>
            <th class="w-12 py-2">选择</th>
            <th class="w-40 py-2">代码/名称</th>
            <th class="w-24 py-2">品种</th>
            <th class="w-16 py-2">市场</th>
            <th class="w-20 py-2">制度</th>
            <th class="py-2">状态与警告</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="candidate in preview.items" :key="candidate.symbol" class="border-t border-border align-top">
            <td class="py-2">
              <input
                type="checkbox"
                class="size-4"
                :aria-label="`选择 ${candidate.symbol}`"
                :checked="selectedSymbols.includes(candidate.symbol)"
                :disabled="!candidate.selectable || candidate.already_monitored || selectMutation.isPending.value"
                @change="toggleCandidate(candidate.symbol, ($event.target as HTMLInputElement).checked)"
              />
            </td>
            <td class="py-2 pr-2">
              <div class="font-medium">{{ candidate.symbol }}</div>
              <div class="break-words text-xs text-muted-foreground">{{ candidate.name }}</div>
            </td>
            <td class="py-2"><Badge>{{ candidateType(candidate) }}</Badge></td>
            <td class="py-2">{{ candidate.exchange ?? '未知' }}</td>
            <td class="py-2">{{ settlementLabel(candidate) }}</td>
            <td class="py-2 text-xs">
              <span v-if="candidate.already_monitored" class="block text-muted-foreground">已监控</span>
              <span
                v-if="candidate.instrument_type === 'etf' && candidate.settlement_cycle === 'unknown'"
                class="block text-amber-700"
              >仅观察，交易制度待确认</span>
              <span v-if="!candidate.selectable" class="block text-red-700">不可选择</span>
              <span v-for="warning in candidate.warnings" :key="warning" class="block text-muted-foreground">
                {{ warning }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="preview?.items.length" class="flex flex-wrap items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">已选择 {{ selectedCount }} 项</span>
      <Button
        variant="primary"
        :loading="selectMutation.isPending.value"
        :disabled="selectedCount === 0"
        @click="selectCandidates"
      >加入监控</Button>
    </div>
  </div>
</template>
