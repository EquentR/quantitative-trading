import { useQuery } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { AuditLog } from '@/api/types'

export const auditLogQueryKey = ['audit'] as const
export const auditEntryQueryKey = (auditId: string) => ['audit', auditId] as const

export function useAuditLogQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: auditLogQueryKey,
    queryFn: () => client.get<AuditLog[]>('/audit'),
  })
}

export function useAuditEntryQuery(auditId: string) {
  const client = useApiClient()
  return useQuery({
    queryKey: auditEntryQueryKey(auditId),
    queryFn: () => client.get<AuditLog>(`/audit/${auditId}`),
  })
}
