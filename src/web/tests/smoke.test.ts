import { render, screen } from '@testing-library/vue'
import { createPinia, setActivePinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { createMemoryHistory, createRouter } from 'vue-router'
import { expect, test } from 'vitest'
import App from '@/App.vue'
import { applyAuthGuard, routes } from '@/router'
import { useSessionStore } from '@/stores/session'

test('renders the local decision-assist console safety copy', async () => {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSessionStore().setToken('test-token')
  const router = createRouter({ history: createMemoryHistory(), routes })
  applyAuthGuard(router)
  await router.push('/')
  await router.isReady()
  render(App, { global: { plugins: [pinia, VueQueryPlugin, router] } })

  expect(screen.getByText(/不自动真实下单/)).toBeInTheDocument()
})
