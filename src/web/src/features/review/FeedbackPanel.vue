<script setup lang="ts">
import { computed, ref } from 'vue'
import Button from '@/components/ui/Button.vue'
import Alert from '@/components/ui/Alert.vue'
import { useRecommendationsQuery } from '@/queries/recommendations'
import { useFeedbackQuery, useRecordFeedbackMutation } from '@/queries/feedback'
import type { ExecutionFeedbackInput } from '@/api/types'

const recommendationsQuery = useRecommendationsQuery()
const recommendations = computed(() => recommendationsQuery.data.value ?? [])

const selectedRecId = ref('')
const executed = ref<'true' | 'false'>('false')
const executionPrice = ref('')
const executionQuantity = ref('')
const note = ref('')
const submitError = ref(false)
const submitSuccess = ref(false)

const feedbackQuery = useFeedbackQuery()
const feedbackRecords = computed(() => feedbackQuery.data.value ?? [])

const recordMutation = useRecordFeedbackMutation()

async function onSubmit() {
  submitError.value = false
  submitSuccess.value = false
  if (!selectedRecId.value) {
    submitError.value = true
    return
  }
  const payload: ExecutionFeedbackInput = {
    recommendation_id: selectedRecId.value,
    executed: executed.value === 'true',
    execution_price: executionPrice.value === '' ? null : Number(executionPrice.value),
    execution_quantity: executionQuantity.value === '' ? null : Number(executionQuantity.value),
    note: note.value,
  }
  try {
    await recordMutation.mutateAsync(payload)
    submitSuccess.value = true
  } catch {
    submitError.value = true
  }
}
</script>

<template>
  <section class="space-y-2">
    <h2 class="text-sm font-medium">人工执行反馈</h2>
    <p class="text-xs text-muted-foreground break-words">
      记录人工执行结果用于复盘，不自动下单，不代表真实成交。
    </p>

    <Alert v-if="submitError" variant="danger">
      <p>反馈提交失败，请稍后重试或检查后端服务状态。</p>
    </Alert>

    <div class="space-y-2 text-sm">
      <div class="flex flex-col gap-1">
        <label for="fb-rec-id" class="text-xs">建议选择</label>
        <select
          id="fb-rec-id"
          v-model="selectedRecId"
          class="rounded-md border border-border px-2 py-1 text-sm"
        >
          <option value="" disabled>请选择建议</option>
          <option v-for="r in recommendations" :key="r.recommendation_id" :value="r.recommendation_id">
            {{ r.symbol }} {{ r.name }} ({{ r.recommendation_id }})
          </option>
        </select>
      </div>

      <div class="flex flex-col gap-1">
        <label for="fb-executed" class="text-xs">是否执行</label>
        <select
          id="fb-executed"
          v-model="executed"
          class="rounded-md border border-border px-2 py-1 text-sm"
        >
          <option value="false">否</option>
          <option value="true">是</option>
        </select>
      </div>

      <div class="flex flex-col gap-1">
        <label for="fb-price" class="text-xs">执行价（可选）</label>
        <input
          id="fb-price"
          v-model="executionPrice"
          type="number"
          step="0.001"
          class="rounded-md border border-border px-2 py-1 text-sm"
          placeholder="留空表示未填写"
        />
      </div>

      <div class="flex flex-col gap-1">
        <label for="fb-quantity" class="text-xs">执行数量（可选）</label>
        <input
          id="fb-quantity"
          v-model="executionQuantity"
          type="number"
          step="1"
          class="rounded-md border border-border px-2 py-1 text-sm"
          placeholder="留空表示未填写"
        />
      </div>

      <div class="flex flex-col gap-1">
        <label for="fb-note" class="text-xs">备注</label>
        <textarea
          id="fb-note"
          v-model="note"
          rows="2"
          class="rounded-md border border-border px-2 py-1 text-sm"
          placeholder="人工备注"
        />
      </div>

      <Button variant="primary" :loading="recordMutation.isPending.value" @click="onSubmit">
        记录人工执行反馈
      </Button>

      <p v-if="submitSuccess" class="text-sm text-emerald-700">已记录反馈</p>
    </div>

    <div v-if="feedbackRecords.length" class="space-y-1">
      <h3 class="text-xs font-medium text-muted-foreground">最近反馈记录</h3>
      <table class="w-full table-fixed text-xs">
        <thead class="text-left text-muted-foreground">
          <tr>
            <th class="w-1/4 py-1">建议ID</th>
            <th class="w-[10%] py-1">执行</th>
            <th class="w-1/4 py-1">价格/数量</th>
            <th class="w-2/5 py-1">备注</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="fb in feedbackRecords"
            :key="fb.feedback_id"
            class="border-t border-border align-top break-words"
          >
            <td class="py-1 break-words">{{ fb.recommendation_id }}</td>
            <td class="py-1">{{ fb.executed ? '是' : '否' }}</td>
            <td class="py-1">{{ fb.execution_price ?? '-' }} / {{ fb.execution_quantity ?? '-' }}</td>
            <td class="py-1 break-words">{{ fb.note }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
