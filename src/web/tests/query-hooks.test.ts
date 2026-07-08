import { VueQueryPlugin } from '@tanstack/vue-query'
import { render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { defineComponent } from 'vue'
import { usePositionsQuery } from '@/queries/positions'
import { useServiceStatusQuery } from '@/queries/service'

test('通过 query hook 读取服务状态', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useServiceStatusQuery() }
    },
    template: '<div>{{ query.data.value?.auth_status }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), VueQueryPlugin] } })

  await waitFor(() => expect(screen.getByText('configured')).toBeInTheDocument())
})

test('通过 query hook 读取持仓列表', async () => {
  const Component = defineComponent({
    setup() {
      return { query: usePositionsQuery() }
    },
    template: '<div>{{ query.data.value?.[0].symbol }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), VueQueryPlugin] } })

  await waitFor(() => expect(screen.getByText('600000')).toBeInTheDocument())
})
