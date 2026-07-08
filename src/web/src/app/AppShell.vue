<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { LayoutDashboard, ListChecks, Activity, ClipboardList, Settings } from 'lucide-vue-next'
import { useServiceStatusQuery } from '@/queries/service'
import { useSessionStore } from '@/stores/session'
import StatusBadges from '@/components/domain/StatusBadges.vue'
import Alert from '@/components/ui/Alert.vue'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const nav = [
  { to: '/', label: '今日仪表盘', icon: LayoutDashboard },
  { to: '/prepare', label: '准备', icon: ListChecks },
  { to: '/monitor', label: '监控', icon: Activity },
  { to: '/review', label: '复盘', icon: ClipboardList },
  { to: '/settings', label: '设置', icon: Settings },
]

const isActive = (to: string) => route.path === to
const isPublicRoute = computed(() => route.path === '/login' || route.path === '/setup')

const serviceQuery = useServiceStatusQuery()
const service = computed(() => serviceQuery.data.value)
const setupRequired = computed(() => service.value?.auth_status === 'setup_required')

watch(
  () => service.value?.auth_status,
  (status) => {
    if (status === 'setup_required' && route.path !== '/setup') {
      router.push('/setup')
    }
  },
)

function onAuthError(event: Event) {
  const code = (event as CustomEvent<{ code?: string }>).detail?.code
  if (code === 'auth_setup_required') {
    router.replace('/setup')
    return
  }
  const redirect = route.path === '/login' && typeof route.query.redirect === 'string'
    ? route.query.redirect
    : route.fullPath
  router.replace({ path: '/login', query: { redirect } })
}

onMounted(() => window.addEventListener('qt-console-auth-error', onAuthError))
onUnmounted(() => window.removeEventListener('qt-console-auth-error', onAuthError))
</script>

<template>
  <RouterView v-if="isPublicRoute" />

  <div v-else class="flex min-h-screen bg-background text-foreground">
    <nav class="hidden w-48 shrink-0 border-r border-border bg-muted/30 md:block">
      <div class="px-3 py-4 text-xs font-medium text-muted-foreground">A 股短线量化控制台</div>
      <ul class="space-y-0.5 px-2">
        <li v-for="item in nav" :key="item.to">
          <RouterLink
            :to="item.to"
            class="flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm hover:bg-muted"
            :class="isActive(item.to) ? 'bg-muted font-medium' : ''"
          >
            <component :is="item.icon" class="size-4 shrink-0" />
            {{ item.label }}
          </RouterLink>
        </li>
      </ul>
    </nav>

    <div class="flex min-w-0 flex-1 flex-col pb-14 md:pb-0">
      <header class="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border px-4 py-2">
        <span class="text-xs text-muted-foreground">只做本地决策辅助，不自动真实下单</span>
        <StatusBadges
          class="ml-auto"
          :auth-status="service?.auth_status"
          :scheduler-running="service?.scheduler_running"
        />
      </header>

      <Alert v-if="setupRequired" variant="warning" class="m-4">
        服务未设置访问密码，请先完成初始化设置。
      </Alert>

      <main class="flex-1 overflow-x-hidden p-4">
        <RouterView />
      </main>
    </div>

    <nav
      class="fixed inset-x-0 bottom-0 border-t border-border bg-background md:hidden"
      aria-label="移动导航"
    >
      <ul class="grid grid-cols-5">
        <li v-for="item in nav" :key="`mobile-${item.to}`">
          <RouterLink
            :to="item.to"
            class="flex min-h-12 flex-col items-center justify-center gap-0.5 text-xs"
            :aria-label="`移动导航 ${item.label}`"
            :class="isActive(item.to) ? 'font-medium text-primary' : 'text-muted-foreground'"
          >
            <component :is="item.icon" class="size-4 shrink-0" />
            <span aria-hidden="true">{{ item.label }}</span>
          </RouterLink>
        </li>
      </ul>
    </nav>
  </div>
</template>
