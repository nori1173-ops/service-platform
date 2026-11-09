<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { NOTE_MAX, VERIFY_METHODS, VERIFY_METHOD_LABELS, maxRule } from '../labels'
import type { VerifyMethod, VerifyPayload } from '../types'

// 別経路の確認の記録つきの操作 (有効に戻す・admin を有効にする)。方法とメモは台帳と監査に残る
const open = defineModel<boolean>({ required: true })

withDefaults(
  defineProps<{
    title: string
    message: string
    warning?: string
    confirmLabel: string
    loading?: boolean
  }>(),
  { warning: '', loading: false },
)

const emit = defineEmits<{ submit: [payload: VerifyPayload] }>()

const form = ref<VForm | null>(null)
const method = ref<VerifyMethod | null>(null)
const note = ref('')
const methodError = ref<string | null>(null)

function reset(): void {
  method.value = null
  note.value = ''
  methodError.value = null
}

watch(open, (value) => value && reset(), { immediate: true })

async function submit(): Promise<void> {
  const result = await form.value?.validate()
  if (!method.value) {
    methodError.value = '確認の方法を選んでください'
    return
  }
  if (!result?.valid) {
    return
  }
  const trimmed = note.value.trim()
  emit('submit', trimmed ? { method: method.value, note: trimmed } : { method: method.value })
}
</script>

<template>
  <v-dialog
    v-model="open"
    max-width="520"
    persistent
  >
    <v-card>
      <v-card-item>
        <v-card-title>{{ title }}</v-card-title>
      </v-card-item>
      <v-card-text>
        <p class="text-body-2 mb-3">
          {{ message }}
        </p>
        <v-alert
          v-if="warning"
          type="warning"
          variant="tonal"
          density="compact"
          class="mb-4"
        >
          {{ warning }}
        </v-alert>
        <v-form
          ref="form"
          @submit.prevent="submit"
        >
          <div class="text-body-2 mb-1">
            別経路での確認の方法
          </div>
          <v-btn-toggle
            v-model="method"
            color="primary"
            variant="outlined"
            density="comfortable"
            class="mb-1"
            @update:model-value="methodError = null"
          >
            <v-btn
              v-for="value in VERIFY_METHODS"
              :key="value"
              :value="value"
              :data-test="`verify-method-${value}`"
            >
              {{ VERIFY_METHOD_LABELS[value] }}
            </v-btn>
          </v-btn-toggle>
          <div
            v-if="methodError"
            data-test="verify-method-error"
            class="text-caption text-error mb-2"
          >
            {{ methodError }}
          </div>
          <v-textarea
            v-model="note"
            data-test="verify-note"
            label="確認のメモ (誰に、何を確かめたか)"
            rows="2"
            auto-grow
            class="mt-3"
            :rules="[maxRule(NOTE_MAX)]"
          />
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="verify-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="verify-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          @click="submit"
        >
          {{ confirmLabel }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
