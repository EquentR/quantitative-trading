<script setup lang="ts">
import { computed } from 'vue'
import Alert from '@/components/ui/Alert.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import { useAuditLogQuery } from '@/queries/audit'
import type { AuditLog } from '@/api/types'

const auditQuery = useAuditLogQuery()
const auditLogs = computed(() => auditQuery.data.value ?? [])
const auditError = computed(() => auditQuery.error.value != null)

function auditSymbol(log: AuditLog): string | null {
  const symbol = log.payload.symbol
  return typeof symbol === 'string' && /^\d{6}$/.test(symbol) ? symbol : null
}
</script>

<template>
  <section class="space-y-2">
    <h2 class="text-sm font-medium">审计日志</h2>
    <Alert v-if="auditError" variant="warning">
      <p>审计日志数据不可用</p>
    </Alert>
    <div v-if="!auditError && auditLogs.length" class="overflow-x-auto">
      <table class="w-full table-fixed text-xs">
        <thead class="text-left text-muted-foreground">
          <tr>
            <th class="w-1/4 py-1">审计ID</th>
            <th class="w-1/4 py-1">事件类型</th>
            <th class="w-1/4 py-1">建议ID</th>
            <th class="w-1/4 py-1">时间</th>
            <th class="w-20 py-1">行情</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="log in auditLogs"
            :key="log.audit_id"
            class="border-t border-border align-top break-words"
          >
            <td class="py-1.5 break-words">{{ log.audit_id }}</td>
            <td class="py-1.5 break-words">{{ log.event_type }}</td>
            <td class="py-1.5 break-words">{{ log.recommendation_id ?? '-' }}</td>
            <td class="py-1.5"><FormatValues kind="time" :value="log.created_at" /></td>
            <td class="py-1.5">
              <RouterLink
                v-if="auditSymbol(log)"
                class="text-primary underline"
                :to="{ path: '/market', query: { symbol: auditSymbol(log) } }"
                :aria-label="`返回 ${auditSymbol(log)} 行情（审计 ${log.audit_id}）`"
              >查看</RouterLink>
              <span v-else class="text-muted-foreground">-</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <p v-if="!auditError && !auditLogs.length" class="text-xs text-muted-foreground">
      加载中或暂无审计日志
    </p>
  </section>
</template>
