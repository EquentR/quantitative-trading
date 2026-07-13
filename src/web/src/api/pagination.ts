import type { PaginatedResponse } from '@/api/types'

export function pageItems<T>(response: PaginatedResponse<T> | T[]): T[] {
  return Array.isArray(response) ? response : response.items
}
