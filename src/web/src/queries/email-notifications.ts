import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import { fetchAllPages } from '@/api/pagination'
import type {
  EmailConnectionTestResult,
  EmailDelivery,
  EmailNotificationSettings,
  EmailNotificationSettingsUpdate,
  EmailTestResult,
} from '@/api/types'

export const emailSettingsQueryKey = ['settings', 'notifications', 'email'] as const
export const emailDeliveriesQueryKey = ['notifications', 'email-deliveries'] as const

export function useEmailSettingsQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: emailSettingsQueryKey,
    queryFn: () => client.get<EmailNotificationSettings>('/settings/notifications/email'),
    retry: false,
  })
}

export function useUpdateEmailSettingsMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (payload: EmailNotificationSettingsUpdate) =>
      client.put<EmailNotificationSettings>('/settings/notifications/email', payload),
    onSuccess: (settings) => queryClient.setQueryData(emailSettingsQueryKey, settings),
  })
}

export function useClearEmailPasswordMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.delete<EmailNotificationSettings>('/settings/notifications/email/password'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: emailSettingsQueryKey }),
  })
}

export function useTestEmailMutation() {
  const client = useApiClient()
  return useMutation({
    mutationFn: () => client.post<EmailTestResult>('/settings/notifications/email/test'),
  })
}

export function useTestEmailConnectionMutation() {
  const client = useApiClient()
  return useMutation({
    mutationFn: () => client.post<EmailConnectionTestResult>('/notifications/email/settings/test-connection'),
  })
}

export function useEmailDeliveriesQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: emailDeliveriesQueryKey,
    queryFn: () => fetchAllPages<EmailDelivery>(
      client,
      '/notifications/email-deliveries',
      { pageSize: 100 },
    ),
    refetchInterval: 30_000,
    retry: false,
  })
}

export function useRetryEmailDeliveryMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (deliveryId: string) =>
      client.post<EmailDelivery>(`/notifications/email-deliveries/${deliveryId}/retry`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: emailDeliveriesQueryKey }),
  })
}
