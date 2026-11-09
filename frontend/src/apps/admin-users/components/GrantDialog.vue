<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { SERVICES, initialStartInput, intervalError, jstToIso, toJstInput } from '../periodLabels'
import type { GrantInput } from '../types'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(
  defineProps<{
    startedAt: string
    account?: string
    orgName?: string
    loading?: boolean
    error?: string | null
  }>(),
  { account: '', orgName: '', loading: false, error: null },
)

const emit = defineEmits<{ submit: [input: GrantInput] }>()

const DEFAULT_SERVICE = SERVICES[0].id

const form = ref<VForm | null>(null)
const service = ref(DEFAULT_SERVICE)
const from = ref('')
const to = ref('')
const intervalMessage = ref<string | null>(null)

const serviceRules = [(value: string) => SERVICES.some((item) => item.id === value) || 'サービスを選んでください']

watch(
  open,
  (value) => {
    if (value) {
      service.value = DEFAULT_SERVICE
      from.value = initialStartInput(props.startedAt)
      to.value = ''
      intervalMessage.value = null
    }
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  if (props.loading) {
    return
  }
  intervalMessage.value = intervalError(from.value, to.value, props.startedAt)
  const result = await form.value?.validate()
  if (intervalMessage.value || !result?.valid || props.loading) {
    return
  }
  emit('submit', {
    service: service.value,
    starts_at: jstToIso(from.value)!,
    ends_at: to.value ? jstToIso(to.value) : null,
  })
}
</script>

<template>
  <v-dialog
    v-model="open"
    max-width="600"
    persistent
  >
    <v-card>
      <v-card-item>
        <v-card-title>サービスの許可を出す</v-card-title>
        <v-card-subtitle>{{ account }} / {{ orgName }}</v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <v-alert
          v-if="error"
          data-test="dialog-error"
          type="error"
          variant="tonal"
          class="mb-4"
        >
          {{ error }}
        </v-alert>
        <v-form
          ref="form"
          :disabled="loading"
          @submit.prevent="submit"
        >
          <v-select
            v-model="service"
            data-test="grant-service"
            label="サービス"
            :items="SERVICES"
            item-title="name"
            item-value="id"
            :rules="serviceRules"
            class="mb-3"
          />
          <v-text-field
            v-model="from"
            data-test="grant-from"
            label="開始 (日本時間 / JST)"
            type="datetime-local"
            step="1"
            :min="toJstInput(startedAt)"
            class="mb-3"
          />
          <v-text-field
            v-model="to"
            data-test="grant-to"
            label="終了 (日本時間 / JST、省略可)"
            type="datetime-local"
            step="1"
          />
        </v-form>
        <v-alert
          v-if="intervalMessage"
          data-test="interval-error"
          type="error"
          variant="tonal"
          density="compact"
          class="mt-3"
        >
          {{ intervalMessage }}
        </v-alert>
        <p class="text-caption text-medium-emphasis mt-3 mb-0">
          終了を省略すると利用期間の終わりまで有効です。終了の時刻は期間に含みません。同じサービスの許可の期間は重ねられません。
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="grant-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="grant-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          :disabled="loading"
          @click="submit"
        >
          許可を出す
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
