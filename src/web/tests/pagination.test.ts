import { expect, test, vi } from 'vitest'
import type { ApiClient } from '@/api/client'
import { fetchAllPages } from '@/api/pagination'

test('fetchAllPages 按 total 读取所有分页并保留顺序', async () => {
  const get = vi.fn(async (path: string) => {
    const page = Number(new URL(path, 'http://local').searchParams.get('page'))
    return {
      items: page === 1 ? ['a', 'b'] : ['c'],
      total: 3,
      page,
      page_size: 2,
    }
  })

  await expect(fetchAllPages(
    { get } as unknown as Pick<ApiClient, 'get'>,
    '/items?status=dead',
    { pageSize: 2 },
  ))
    .resolves.toEqual(['a', 'b', 'c'])
  expect(get).toHaveBeenNthCalledWith(1, '/items?status=dead&page=1&page_size=2')
  expect(get).toHaveBeenNthCalledWith(2, '/items?status=dead&page=2&page_size=2')
})
