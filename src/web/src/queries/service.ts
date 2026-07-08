import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import { useApiClient } from '@/api/client-provider'
import type { ServiceStatus } from '@/api/types'

export const serviceStatusQueryKey = ['service', 'status'] as const

export function useServiceStatusQuery() {
  const client = useApiClient()
  return useQuery({
    queryKey: serviceStatusQueryKey,
    queryFn: () => client.get<ServiceStatus>('/service/status'),
    refetchInterval: 30_000,
  })
}

export function useStartSchedulerMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.post<ServiceStatus>('/service/scheduler/start'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: serviceStatusQueryKey }),
  })
}

export function useStopSchedulerMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.post<ServiceStatus>('/service/scheduler/stop'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: serviceStatusQueryKey }),
  })
}

export function useRunOnceMutation() {
  const client = useApiClient()
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => client.post<ServiceStatus>('/service/run-once'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: serviceStatusQueryKey })
      queryClient.invalidateQueries({ queryKey: ['account', 'snapshot'] })
    },
  })
}
