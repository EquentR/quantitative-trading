<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { X } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import Alert from '@/components/ui/Alert.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import RecommendationStatusBadge from '@/components/domain/RecommendationStatusBadge.vue'
import { useNotificationsQuery } from '@/queries/notifications'
import { useAuditLogQuery } from '@/queries/audit'
import type { Recommendation } from '@/api/types'

interface Props {
  recommendation: Recommendation
}
const props = defineProps<Props>()
const emit = defineEmits<{ close: [] }>()

const rec = computed(() => props.recommendation)
const risk = computed(() => (rec.value.risk as Record<string, unknown>) ?? {})

const invalidIf = computed<string[]>(() => {
  const v = risk.value.invalid_if
  return Array.isArray(v) ? (v as string[]) : []
})
const positionLimit = computed(() => {
  const v = risk.value.position_limit
  return typeof v === 'string' ? v : v == null ? '' : String(v)
})
const riskNotes = computed<string[]>(() => {
  const v = risk.value.notes
  return Array.isArray(v) ? (v as string[]) : []
})

const actionRequiresInvalidIf = computed(() =>
  ['buy', 'add', 'hold', 'watch'].includes(rec.value.action),
)
const missingInvalidIf = computed(() => actionRequiresInvalidIf.value && invalidIf.value.length === 0)
const missingDataTime = computed(() => !rec.value.data_time)
const contractError = computed(() => missingInvalidIf.value || missingDataTime.value)

const notificationsQuery = useNotificationsQuery()
const auditQuery = useAuditLogQuery()

const notificationsAvailable = computed(
  () => !notificationsQuery.error.value && !!notificationsQuery.data.value,
)
const notification = computed(() =>
  (notificationsQuery.data.value ?? []).find(
    (n) => n.recommendation_id === rec.value.recommendation_id,
  ) ?? null,
)
const auditEntry = computed(() => {
  if (!notification.value?.audit_id) return null
  return (auditQuery.data.value ?? []).find((a) => a.audit_id === notification.value?.audit_id) ?? null
})
const auditAvailable = computed(() => !!auditEntry.value)

const dialogEl = ref<HTMLElement | null>(null)

function entries(obj: Record<string, unknown> | undefined): [string, unknown][] {
  if (!obj || typeof obj !== 'object') return []
  return Object.entries(obj)
}

function valueText(v: unknown): string {
  if (v === null || v === undefined) return '不可用'
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (typeof v === 'number' || typeof v === 'string') return String(v)
  return JSON.stringify(v)
}

function onClose() {
  emit('close')
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    e.preventDefault()
    e.stopPropagation()
    onClose()
  }
}

onMounted(() => {
  document.addEventListener('keydown', onKeydown, true)
  nextTick(() => {
    const btn = dialogEl.value?.querySelector<HTMLButtonElement>('[aria-label="关闭详情"]')
    btn?.focus()
  })
})

onUnmounted(() => {
  document.removeEventListener('keydown', onKeydown, true)
})
</script>

<template>
  <div class="fixed inset-0 z-30 flex justify-end">
    <div class="absolute inset-0 bg-black/30" @click="onClose" />

    <aside
      ref="dialogEl"
      class="relative flex h-full w-full max-w-sm flex-col gap-4 overflow-y-auto border-l border-border bg-background p-4 shadow-lg"
      role="dialog"
      aria-modal="true"
      aria-label="建议详情"
      tabindex="-1"
      :data-data-time="rec.data_time || '不可用'"
      @keydown.esc="onClose"
    >
      <header class="flex items-start justify-between gap-2">
        <div>
          <h2 class="text-base font-semibold break-words">{{ rec.symbol }} {{ rec.name }}</h2>
          <div class="mt-1.5 flex flex-wrap gap-1.5">
            <RecommendationStatusBadge kind="action" :value="rec.action" />
            <RecommendationStatusBadge kind="confidence" :value="rec.confidence" />
            <RecommendationStatusBadge
              v-if="notificationsAvailable && notification"
              kind="status"
              :value="notification.status"
            />
          </div>
        </div>
        <Button variant="ghost" aria-label="关闭详情" @click="onClose">
          <X class="size-4" />
        </Button>
      </header>

      <Alert v-if="contractError" variant="danger" data-testid="recommendation-contract-error">
        本条建议缺少必要字段{{ missingInvalidIf ? '：失效条件' : '' }}{{ missingDataTime ? '、数据时间' : '' }}，不可作为完整建议展示。
      </Alert>

      <template v-else>
        <section class="space-y-1">
          <h3 class="text-sm font-medium">理由</h3>
          <ul v-if="rec.reason.length" class="list-disc space-y-0.5 pl-4 text-sm break-words">
            <li v-for="(reason, idx) in rec.reason" :key="idx">{{ reason }}</li>
          </ul>
          <p v-else class="text-sm text-muted-foreground">暂无理由</p>
        </section>

        <section class="space-y-1">
          <h3 class="text-sm font-medium">风险</h3>
          <ul v-if="riskNotes.length" class="list-disc space-y-0.5 pl-4 text-sm break-words">
            <li v-for="(note, idx) in riskNotes" :key="idx">{{ note }}</li>
          </ul>
          <p v-else class="text-sm text-muted-foreground">暂无额外风险说明</p>
        </section>

        <section class="space-y-1">
          <h3 class="text-sm font-medium">失效条件</h3>
          <ul v-if="invalidIf.length" class="list-disc space-y-0.5 pl-4 text-sm break-words">
            <li v-for="(cond, idx) in invalidIf" :key="idx">{{ cond }}</li>
          </ul>
          <p v-else class="text-sm text-muted-foreground">不适用</p>
        </section>

        <section class="space-y-1">
          <h3 class="text-sm font-medium">仓位约束</h3>
          <p v-if="positionLimit" class="text-sm break-words">{{ positionLimit }}</p>
          <p v-else class="text-sm text-muted-foreground">未提供仓位约束说明</p>
        </section>

        <section class="space-y-1">
          <h3 class="text-sm font-medium">价格上下文</h3>
          <dl v-if="entries(rec.price_context as Record<string, unknown>).length" class="text-sm">
            <div v-for="[key, val] in entries(rec.price_context as Record<string, unknown>)" :key="key" class="break-all">
              <dt class="inline font-medium">{{ key }}:</dt>
              <dd class="inline">{{ ' ' + valueText(val) }}</dd>
            </div>
          </dl>
          <p v-else class="text-sm text-muted-foreground">价格上下文不可用</p>
        </section>

        <section class="space-y-1">
          <h3 class="text-sm font-medium">账户上下文</h3>
          <dl v-if="entries(rec.account_context as Record<string, unknown>).length" class="text-sm">
            <div v-for="[key, val] in entries(rec.account_context as Record<string, unknown>)" :key="key" class="break-all">
              <dt class="inline font-medium">{{ key }}:</dt>
              <dd class="inline">{{ ' ' + valueText(val) }}</dd>
            </div>
          </dl>
          <p v-else class="text-sm text-muted-foreground">账户上下文不可用</p>
        </section>

        <section class="space-y-1">
          <h3 class="text-sm font-medium">持仓上下文</h3>
          <dl v-if="entries(rec.position_context as Record<string, unknown>).length" class="text-sm">
            <div v-for="[key, val] in entries(rec.position_context as Record<string, unknown>)" :key="key" class="break-all">
              <dt class="inline font-medium">{{ key }}:</dt>
              <dd class="inline">{{ ' ' + valueText(val) }}</dd>
            </div>
          </dl>
          <p v-else class="text-sm text-muted-foreground">持仓上下文不可用</p>
        </section>

        <section class="space-y-1">
          <h3 class="text-sm font-medium">数据引用</h3>
          <dl class="text-sm">
            <div>
              <dt class="inline font-medium">数据时间:</dt>
              <dd class="inline"><FormatValues kind="time" :value="rec.data_time" /></dd>
            </div>
            <div>
              <dt class="inline font-medium">有效期至:</dt>
              <dd class="inline"><FormatValues kind="time" :value="rec.valid_until" /></dd>
            </div>
          </dl>
        </section>

        <section class="space-y-1">
          <h3 class="text-sm font-medium">审计引用</h3>
          <dl v-if="auditAvailable && auditEntry" class="text-sm">
            <div>
              <dt class="inline font-medium">审计 ID:</dt>
              <dd class="inline break-all">{{ auditEntry.audit_id }}</dd>
            </div>
            <div>
              <dt class="inline font-medium">事件类型:</dt>
              <dd class="inline break-words">{{ auditEntry.event_type }}</dd>
            </div>
            <div>
              <dt class="inline font-medium">记录时间:</dt>
              <dd class="inline"><FormatValues kind="time" :value="auditEntry.created_at" /></dd>
            </div>
          </dl>
          <p v-else class="text-sm text-muted-foreground">审计数据不可用</p>
        </section>
      </template>
    </aside>
  </div>
</template>
