<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Save, LogOut } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import { useSessionStore } from '@/stores/session'
import { useLogoutMutation } from '@/queries/auth'
import EmailNotificationPanel from './EmailNotificationPanel.vue'

const session = useSessionStore()
const router = useRouter()
const logoutMutation = useLogoutMutation()

const apiUrl = ref(session.apiBaseUrl)

function onSave() {
  session.setApiBaseUrl(apiUrl.value)
}

async function onLogout() {
  try {
    await logoutMutation.mutateAsync()
  } catch {
    // 即使后端调用失败也只清除前端 token
  }
  session.clearToken()
  router.push('/login')
}
</script>

<template>
  <div class="space-y-4">
    <h1 class="text-lg font-semibold">设置</h1>

    <section class="space-y-3">
      <h2 class="text-sm font-medium">本地连接</h2>
      <label class="block">
        <span class="text-sm font-medium">API 地址</span>
        <input
          v-model="apiUrl"
          class="mt-1 block w-full max-w-md rounded-md border border-border px-3 py-1.5 text-sm"
          placeholder="http://127.0.0.1:8000"
        />
      </label>
      <Button variant="primary" @click="onSave">
        <Save class="size-4" />
        保存本地设置
      </Button>
    </section>

    <section class="space-y-3">
      <h2 class="text-sm font-medium">会话</h2>
      <p class="text-xs text-muted-foreground">本控制台不保存明文访问密码。</p>
      <Button variant="danger" :loading="logoutMutation.isPending.value" @click="onLogout">
        <LogOut class="size-4" />
        退出登录
      </Button>
      <p class="text-xs text-muted-foreground">退出登录只清除前端 token，不暗示后端 token 已撤销。</p>
    </section>

    <EmailNotificationPanel />
  </div>
</template>
