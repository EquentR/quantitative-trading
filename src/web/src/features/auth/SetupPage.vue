<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useQueryClient } from '@tanstack/vue-query'
import { KeyRound } from 'lucide-vue-next'
import Button from '@/components/ui/Button.vue'
import { useSetupPasswordMutation } from '@/queries/auth'

const setup = useSetupPasswordMutation()
const router = useRouter()
const queryClient = useQueryClient()
const password = ref('')
const success = ref(false)

async function onSubmit() {
  await setup.mutateAsync({ password: password.value })
  await queryClient.invalidateQueries({ queryKey: ['service', 'status'] })
  success.value = true
}

function goLogin() {
  router.push('/login')
}
</script>

<template>
  <div class="mx-auto flex min-h-screen max-w-sm flex-col justify-center px-6">
    <h1 class="text-xl font-semibold">设置访问密码</h1>
    <p class="mt-2 text-sm text-muted-foreground">
      密码用于保护本地 HTTP API，不会保存明文。
    </p>

    <form class="mt-6 space-y-4" @submit.prevent="onSubmit">
      <label class="block">
        <span class="text-sm font-medium">设置访问密码</span>
        <input
          v-model="password"
          type="password"
          class="mt-1 block w-full rounded-md border border-border px-3 py-1.5 text-sm"
          autocomplete="new-password"
        />
      </label>

      <Button type="submit" variant="primary" :loading="setup.isPending.value">
        <KeyRound class="size-4" />
        设置本地访问密码
      </Button>
    </form>

    <p v-if="success" class="mt-4 text-sm text-emerald-700">访问密码已设置</p>
    <Button v-if="success" class="mt-3" type="button" variant="secondary" @click="goLogin">
      前往登录
    </Button>
    <p v-if="setup.isError.value" class="mt-4 text-sm text-red-700">设置失败，请重试。</p>
  </div>
</template>
