import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { NotificationProcessingStatus, NotificationSummary } from '@/api/types'

export const notificationsQueryKey = ['notifications'] as const
export const notificationQueryKey = (notificationId: string) => ['notifications', notificationId] as const

function invalidateNotificationDependents(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: notificationsQueryKey })
  queryClient.invalidateQueries({ queryKey: ['recommendations'] })
}

export function useNotificationsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: notificationsQueryKey,
    queryFn: () => client.get<NotificationSummary[]>('/notifications'),
  })
}

export function useMarkNotificationReadMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (notificationId: string) =>
      client.post<{ status: NotificationProcessingStatus }>(`/notifications/${notificationId}/read`),
    onSuccess: () => invalidateNotificationDependents(queryClient),
  })
}
