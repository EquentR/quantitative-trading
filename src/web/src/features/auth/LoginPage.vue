<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { LogIn } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import { useLoginMutation } from '@/queries/auth'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const route = useRoute()
const session = useSessionStore()
const login = useLoginMutation()

const password = ref('')
const success = ref(false)
const failed = ref(false)

function normalizeRedirect(value: unknown): string {
  if (typeof value !== 'string' || !value.startsWith('/')) return '/'
  if (!value.startsWith('/login')) return value

  const url = new URL(value, 'http://local')
  const nested = url.searchParams.get('redirect')
  return nested?.startsWith('/') && !nested.startsWith('/login') ? nested : '/'
}

async function onSubmit() {
  failed.value = false
  success.value = false
  try {
    const result = await login.mutateAsync({ password: password.value })
    session.setToken(result.access_token)
    success.value = true
    router.push(normalizeRedirect(route.query.redirect))
  } catch {
    failed.value = true
  }
}
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-6">
    <h1 class="text-xl font-semibold">登录本地控制台</h1>
    <p class="mt-2 text-sm text-muted-foreground">
      登录只用于访问本地 HTTP API，不连接真实券商账户。
    </p>

    <form class="mt-6 space-y-4" @submit.prevent="onSubmit">
      <label class="block">
        <span class="text-sm font-medium">访问密码</span>
        <input
          v-model="password"
          type="password"
          class="mt-1 block w-full rounded-md border border-border px-3 py-1.5 text-sm"
          autocomplete="current-password"
        />
      </label>

      <Button type="submit" variant="primary" :loading="login.isPending.value">
        <LogIn class="size-4" />
        登录本地控制台
      </Button>
    </form>

    <p v-if="success" class="mt-4 text-sm text-emerald-700">已登录本地控制台</p>
    <p v-if="failed" class="mt-4 text-sm text-red-700">登录失败，请检查访问密码。</p>
  </div>
</template>
