<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { NOTE_MAX, ORG_NAME_MAX, maxRule } from '../labels'
import { ACCESS_DESCRIPTIONS, ACCESS_LABELS, HANDOVER_NOTE } from '../periodLabels'
import type { DeviceAccessMode, StartDevicePeriodInput } from '../types'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(defineProps<{ loading?: boolean; error?: string | null }>(), {
  loading: false,
  error: null,
})

const emit = defineEmits<{ submit: [input: StartDevicePeriodInput] }>()

const form = ref<VForm | null>(null)
const account = ref('')
const orgName = ref('')
const note = ref('')
const access = ref<DeviceAccessMode>('everyone')
const ACCESS_CHOICES: DeviceAccessMode[] = ['everyone', 'assigned']

const accountRules = [(value: string) => /^[0-9]{8}$/.test(value) || '半角数字 8 桁で入力してください']
const orgRules = [(value: string) => !!value.trim() || '組織名を入力してください', maxRule(ORG_NAME_MAX)]
const noteRules = [maxRule(NOTE_MAX)]

watch(
  open,
  (value) => {
    if (value) {
      account.value = ''
      orgName.value = ''
      note.value = ''
      access.value = 'everyone'
    }
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  if (props.loading) {
    return
  }
  const result = await form.value?.validate()
  if (!result?.valid || props.loading) {
    return
  }
  const trimmedNote = note.value.trim()
  emit('submit', {
    account: account.value,
    org_name: orgName.value.trim(),
    ...(trimmedNote ? { note: trimmedNote } : {}),
    access: access.value,
  })
}
</script>

<template>
  <v-dialog
    v-model="open"
    max-width="560"
    persistent
  >
    <v-card>
      <v-card-item>
        <v-card-title>通信機の利用期間を始める</v-card-title>
      </v-card-item>
      <v-card-text>
        <v-alert
          v-if="error"
          data-test="start-error"
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
          <v-text-field
            v-model="account"
            data-test="period-account"
            label="通信機アカウント (半角数字 8 桁)"
            inputmode="numeric"
            :rules="accountRules"
            class="mb-3"
          />
          <v-text-field
            v-model="orgName"
            data-test="period-org"
            label="組織名"
            :rules="orgRules"
            class="mb-3"
          />
          <v-textarea
            v-model="note"
            data-test="period-note"
            label="メモ (省略可)"
            rows="2"
            auto-grow
            :rules="noteRules"
          />
          <div class="text-body-2 mt-2 mb-1">
            通信機ログインに進める人
          </div>
          <v-btn-toggle
            v-model="access"
            mandatory
            density="compact"
            variant="outlined"
            color="primary"
          >
            <v-btn
              v-for="choice in ACCESS_CHOICES"
              :key="choice"
              :value="choice"
              :data-test="`period-access-${choice}`"
            >
              {{ ACCESS_LABELS[choice] }}
            </v-btn>
          </v-btn-toggle>
          <p class="text-caption text-medium-emphasis mt-1 mb-2">
            {{ ACCESS_DESCRIPTIONS[access] }}
          </p>
          <v-alert
            data-test="period-access-note"
            type="warning"
            variant="tonal"
            density="compact"
            class="mb-2"
          >
            {{ HANDOVER_NOTE }}
          </v-alert>
        </v-form>
        <p class="text-caption text-medium-emphasis mb-0">
          同じ通信機に稼働中の利用期間があるときは始められません。次のお客様へ渡すときは、先に今の利用期間を終えてください。
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="period-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="period-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          :disabled="loading"
          @click="submit"
        >
          始める
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
