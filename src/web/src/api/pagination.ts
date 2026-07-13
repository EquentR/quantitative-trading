import type { PaginatedResponse } from '@/api/types'
import type { ApiClient } from '@/api/client'

export function pageItems<T>(response: PaginatedResponse<T> | T[]): T[] {
  return Array.isArray(response) ? response : response.items
}

export async function fetchAllPages<T>(
  client: Pick<ApiClient, 'get'>,
  path: string,
  { pageSize }: { pageSize: number },
): Promise<T[]> {
  const separator = path.includes('?') ? '&' : '?'
  const first = await client.get<PaginatedResponse<T> | T[]>(
    `${path}${separator}page=1&page_size=${pageSize}`,
  )
  if (Array.isArray(first) || first.items.length >= first.total) return pageItems(first)

  const pageCount = Math.ceil(first.total / pageSize)
  const remaining = await Promise.all(
    Array.from({ length: pageCount - 1 }, (_, index) =>
      client.get<PaginatedResponse<T>>(
        `${path}${separator}page=${index + 2}&page_size=${pageSize}`,
      ),
    ),
  )
  return [first, ...remaining].flatMap((page) => page.items)
}
