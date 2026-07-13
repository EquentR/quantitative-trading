<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { MailCheck, RefreshCw, Save, Send, Trash2 } from 'lucide-vue-next'
import Alert from '@/components/ui/Alert.vue'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import FormatValues from '@/components/domain/FormatValues.vue'
import type { EmailNotificationSettingsUpdate, EmailSecurityMode } from '@/api/types'
import {
  useClearEmailPasswordMutation,
  useEmailDeliveriesQuery,
  useEmailSettingsQuery,
  useRetryEmailDeliveryMutation,
  useTestEmailConnectionMutation,
  useTestEmailMutation,
  useUpdateEmailSettingsMutation,
} from '@/queries/email-notifications'

const settingsQuery = useEmailSettingsQuery()
const deliveriesQuery = useEmailDeliveriesQuery()
const updateMutation = useUpdateEmailSettingsMutation()
const clearPasswordMutation = useClearEmailPasswordMutation()
const connectionTestMutation = useTestEmailConnectionMutation()
const testEmailMutation = useTestEmailMutation()
const retryMutation = useRetryEmailDeliveryMutation()

const host = ref('')
const port = ref(587)
const username = ref('')
const password = ref('')
const sender = ref('')
const recipient = ref('')
const securityMode = ref<EmailSecurityMode>('starttls')
const enabled = ref(false)
const saveMessage = ref('')
const passwordMessage = ref('')
const connectionTestMessage = ref('')
const testEmailMessage = ref('')
const retryMessage = ref('')

watch(
  () => settingsQuery.data.value,
  (settings) => {
    if (!settings) return
    host.value = settings.host
    port.value = settings.port
    username.value = settings.username
    sender.value = settings.sender
    recipient.value = settings.recipient
    securityMode.value = settings.security
    enabled.value = settings.enabled
    password.value = ''
  },
  { immediate: true },
)

const failedDeliveries = computed(() =>
  (deliveriesQuery.data.value ?? []).filter((delivery) =>
    delivery.status === 'dead' || delivery.status === 'retry',
  ),
)

async function saveSettings() {
  saveMessage.value = ''
  const payload: EmailNotificationSettingsUpdate = {
    host: host.value.trim(),
    port: Number(port.value),
    username: username.value.trim(),
    sender: sender.value.trim(),
    recipient: recipient.value.trim(),
    security: securityMode.value,
    enabled: enabled.value,
  }
  if (password.value !== '') payload.password = password.value

  try {
    await updateMutation.mutateAsync(payload)
    password.value = ''
    saveMessage.value = '邮件配置已保存'
  } catch {
    saveMessage.value = '邮件配置保存失败，请检查输入和本地服务状态'
  }
}

async function clearPassword() {
  passwordMessage.value = ''
  try {
    await clearPasswordMutation.mutateAsync()
    password.value = ''
    passwordMessage.value = 'SMTP 密码已清除'
  } catch {
    passwordMessage.value = 'SMTP 密码清除失败'
  }
}

async function testConnection() {
  connectionTestMessage.value = ''
  try {
    await connectionTestMutation.mutateAsync()
    connectionTestMessage.value = '连接成功'
  } catch {
    connectionTestMessage.value = 'SMTP 连接测试失败'
  }
}

async function sendTestEmail() {
  testEmailMessage.value = ''
  try {
    await testEmailMutation.mutateAsync()
    testEmailMessage.value = '测试邮件已发送'
  } catch {
    testEmailMessage.value = '测试邮件发送失败'
  }
}

async function retryDelivery(deliveryId: string) {
  retryMessage.value = ''
  try {
    await retryMutation.mutateAsync(deliveryId)
    retryMessage.value = `已重新提交 ${deliveryId}`
  } catch {
    retryMessage.value = `重试 ${deliveryId} 失败`
  }
}

function channelText(): string {
  const settings = settingsQuery.data.value
  if (!settings) return '邮件通道状态不可用'
  if (!settings.configured) return '邮件配置未保存'
  if (!settings.enabled) return '邮件通道未启用'
  if (!settings.password_configured) return '邮件通道缺少密码'
  return '邮件通道已启用'
}
</script>

<template>
  <section class="min-w-0 space-y-3 border-t border-border pt-4">
    <div class="flex flex-wrap items-center gap-2">
      <h2 v-if="!settingsQuery.isPending.value" class="text-base font-semibold">邮件通知</h2>
      <span v-else class="text-base font-semibold">邮件配置</span>
      <Badge
        v-if="settingsQuery.data.value"
        :variant="settingsQuery.data.value.configured && settingsQuery.data.value.enabled && settingsQuery.data.value.password_configured ? 'success' : 'warning'"
      >
        {{ channelText() }}
      </Badge>
      <Badge v-if="settingsQuery.data.value?.password_configured" variant="success">密码已配置</Badge>
      <Badge v-else-if="settingsQuery.data.value" variant="warning">密码未配置</Badge>
    </div>

    <Alert variant="warning">
      <p class="break-words">SMTP 密码按用户确认以明文保存在本地 SQLite；数据库导出和备份会包含 SMTP 明文密码。</p>
    </Alert>
    <p class="text-xs text-muted-foreground">邮件通道禁用、未配置或投递失败时，本地通知仍可正常使用。</p>

    <p v-if="settingsQuery.isPending.value" class="text-sm text-muted-foreground">正在加载邮件配置</p>
    <Alert v-else-if="settingsQuery.error.value" variant="danger">
      <div class="flex flex-wrap items-center gap-2">
        <span>邮件配置加载失败</span>
        <Button @click="settingsQuery.refetch()"><RefreshCw class="size-4" />重试配置</Button>
      </div>
    </Alert>

    <form v-else class="space-y-3" @submit.prevent="saveSettings">
      <div class="grid gap-3 md:grid-cols-2">
        <label class="block text-sm font-medium">
          SMTP 主机
          <input v-model="host" required class="mt-1 block w-full rounded-md border border-border px-3 py-1.5 text-sm" autocomplete="off" />
        </label>
        <label class="block text-sm font-medium">
          SMTP 端口
          <input v-model.number="port" required type="number" min="1" max="65535" class="mt-1 block w-full rounded-md border border-border px-3 py-1.5 text-sm" />
        </label>
        <label class="block text-sm font-medium">
          SMTP 用户名
          <input v-model="username" class="mt-1 block w-full rounded-md border border-border px-3 py-1.5 text-sm" autocomplete="off" />
        </label>
        <label class="block text-sm font-medium">
          SMTP 密码
          <input
            v-model="password"
            type="password"
            class="mt-1 block w-full rounded-md border border-border px-3 py-1.5 text-sm"
            autocomplete="new-password"
            placeholder="留空保留当前密码"
          />
        </label>
        <label class="block text-sm font-medium">
          发件人
          <input v-model="sender" required type="email" class="mt-1 block w-full rounded-md border border-border px-3 py-1.5 text-sm" autocomplete="off" />
        </label>
        <label class="block text-sm font-medium">
          收件人
          <input v-model="recipient" required type="email" class="mt-1 block w-full rounded-md border border-border px-3 py-1.5 text-sm" autocomplete="off" />
        </label>
        <label class="block text-sm font-medium">
          连接模式
          <select v-model="securityMode" class="mt-1 block w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm">
            <option value="none">none</option>
            <option value="starttls">starttls</option>
            <option value="ssl">ssl</option>
          </select>
        </label>
        <label class="flex items-center gap-2 self-end pb-2 text-sm font-medium">
          <input v-model="enabled" type="checkbox" class="size-4" />
          启用邮件通道
        </label>
      </div>

      <div class="grid gap-2 sm:grid-cols-2 lg:flex">
        <Button type="submit" variant="primary" :loading="updateMutation.isPending.value">
          <Save class="size-4" />保存邮件配置
        </Button>
        <Button type="button" variant="danger" :loading="clearPasswordMutation.isPending.value" @click="clearPassword">
          <Trash2 class="size-4" />清除 SMTP 密码
        </Button>
        <Button type="button" :loading="connectionTestMutation.isPending.value" @click="testConnection">
          <MailCheck class="size-4" />测试 SMTP 连接
        </Button>
        <Button type="button" :loading="testEmailMutation.isPending.value" @click="sendTestEmail">
          <Send class="size-4" />发送测试邮件
        </Button>
      </div>
      <p v-if="saveMessage" class="text-sm" role="status">{{ saveMessage }}</p>
      <p v-if="passwordMessage" class="text-sm" role="status">{{ passwordMessage }}</p>
      <p v-if="connectionTestMessage" class="text-sm" role="status">{{ connectionTestMessage }}</p>
      <p v-if="testEmailMessage" class="text-sm" role="status">{{ testEmailMessage }}</p>
    </form>

    <section class="space-y-2 border-t border-border pt-3">
      <h3 class="text-sm font-medium">失败投递</h3>
      <p v-if="deliveriesQuery.isPending.value" class="text-sm text-muted-foreground">正在加载失败投递</p>
      <Alert v-else-if="deliveriesQuery.error.value" variant="danger">
        <div class="flex flex-wrap items-center gap-2">
          <span>失败投递列表加载失败</span>
          <Button @click="deliveriesQuery.refetch()"><RefreshCw class="size-4" />重试列表</Button>
        </div>
      </Alert>
      <p v-else-if="failedDeliveries.length === 0" class="text-sm text-muted-foreground">当前没有失败邮件投递</p>
      <div v-else class="table-scroll">
        <table class="w-full table-fixed text-xs md:min-w-[48rem]" aria-label="失败邮件投递">
          <thead class="text-left text-muted-foreground"><tr>
            <th class="w-1/4 py-1 md:w-1/6">投递 ID</th><th class="hidden w-1/6 md:table-cell">收件人</th><th class="hidden w-1/5 md:table-cell">主题</th><th class="w-[14%] md:w-[10%]">状态</th><th class="hidden w-[9%] md:table-cell">尝试</th><th>安全错误摘要</th><th class="hidden w-1/6 md:table-cell">创建时间</th><th class="w-12 md:w-24">操作</th>
          </tr></thead>
          <tbody><tr v-for="delivery in failedDeliveries" :key="delivery.delivery_id" class="border-t border-border align-top">
            <td class="py-1.5 break-all">{{ delivery.delivery_id }}</td><td class="hidden break-all md:table-cell">{{ delivery.recipient }}</td><td class="hidden break-words md:table-cell">{{ delivery.subject }}</td>
            <td class="break-words">{{ delivery.status }}</td><td class="hidden md:table-cell">{{ delivery.attempt_count }}</td><td class="break-words">{{ delivery.last_error ?? '不可用' }}</td>
            <td class="hidden md:table-cell"><FormatValues kind="time" :value="delivery.created_at" /></td>
            <td><Button :aria-label="`重试 ${delivery.delivery_id}`" :loading="retryMutation.isPending.value" @click="retryDelivery(delivery.delivery_id)"><RefreshCw class="size-4" /><span class="sr-only">重试 {{ delivery.delivery_id }}</span></Button></td>
          </tr></tbody>
        </table>
      </div>
      <p v-if="retryMessage" class="text-sm" role="status">{{ retryMessage }}</p>
    </section>
  </section>
</template>
