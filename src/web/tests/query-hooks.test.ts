import { VueQueryPlugin, type VueQueryPluginOptions } from '@tanstack/vue-query'
import { render, screen, waitFor } from '@testing-library/vue'
import { createPinia } from 'pinia'
import { defineComponent } from 'vue'
import { usePositionsQuery } from '@/queries/positions'
import { useServiceStatusQuery } from '@/queries/service'
import { useWatchlistPinnedQuery } from '@/queries/watchlist'
import { useUniverseQuery } from '@/queries/universe'
import { useDatasourceStatusQuery } from '@/queries/datasource'
import { useLatestPlanQuery } from '@/queries/plans'
import { useRecommendationsQuery } from '@/queries/recommendations'
import { useNotificationsQuery } from '@/queries/notifications'
import { useAuditLogQuery } from '@/queries/audit'
import { useFeedbackQuery } from '@/queries/feedback'

const queryPlugin: VueQueryPluginOptions = {}

test('通过 query hook 读取服务状态', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useServiceStatusQuery() }
    },
    template: '<div>{{ query.data.value?.auth_status }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('configured')).toBeInTheDocument())
})

test('通过 query hook 读取服务状态 Task 11 字段', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useServiceStatusQuery() }
    },
    template: '<div>{{ query.data.value?.last_task_type }}|{{ query.data.value?.last_plan_id }}|{{ query.data.value?.last_recommendation_ids?.join(",") }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('plan_generation|plan-001|rec-001')).toBeInTheDocument())
})

test('通过 query hook 读取持仓列表', async () => {
  const Component = defineComponent({
    setup() {
      return { query: usePositionsQuery() }
    },
    template: '<div>{{ query.data.value?.[0].symbol }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('600000')).toBeInTheDocument())
})

test('通过 query hook 读取自选置顶', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useWatchlistPinnedQuery() }
    },
    template: '<div>{{ query.data.value?.[0].symbol }}-{{ query.data.value?.[0].name }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('600519-示例白酒')).toBeInTheDocument())
})

test('通过 query hook 读取股票池', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useUniverseQuery() }
    },
    template: '<div>{{ query.data.value?.[0].symbol }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('600000')).toBeInTheDocument())
})

test('通过 query hook 读取数据源状态', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useDatasourceStatusQuery() }
    },
    template: '<div>{{ query.data.value?.provider }}-{{ query.data.value?.status }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('eastmoney-missing')).toBeInTheDocument())
})

test('通过 query hook 读取最新计划', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useLatestPlanQuery() }
    },
    template: '<div>{{ query.data.value?.plan_id }}-{{ query.data.value?.status }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('plan-001-active')).toBeInTheDocument())
})

test('通过 query hook 读取建议列表', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useRecommendationsQuery() }
    },
    template: '<div>{{ query.data.value?.[0].recommendation_id }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('rec-001')).toBeInTheDocument())
})

test('通过 query hook 读取通知列表', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useNotificationsQuery() }
    },
    template: '<div>{{ query.data.value?.[0].notification_id }}-{{ query.data.value?.[0].status }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('notif-001-unread')).toBeInTheDocument())
})

test('通过 query hook 读取审计日志列表', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useAuditLogQuery() }
    },
    template: '<div>{{ query.data.value?.[0]?.audit_id }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('audit-001')).toBeInTheDocument())
})

test('通过 query hook 读取执行反馈', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useFeedbackQuery('rec-001') }
    },
    template: '<div>{{ query.data.value?.[0].feedback_id }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('fb-001')).toBeInTheDocument())
})

test('通过 query hook 读取全部执行反馈', async () => {
  const Component = defineComponent({
    setup() {
      return { query: useFeedbackQuery() }
    },
    template: '<div>{{ query.data.value?.[0].feedback_id }}</div>',
  })

  render(Component, { global: { plugins: [createPinia(), [VueQueryPlugin, queryPlugin]] } })

  await waitFor(() => expect(screen.getByText('fb-001')).toBeInTheDocument())
})
