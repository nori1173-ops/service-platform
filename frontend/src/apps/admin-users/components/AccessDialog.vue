<script setup lang="ts">
import { ref, watch } from 'vue'
import { DASH } from '@/utils/format'
import { formatJst } from '../labels'
import { ACCESS_DESCRIPTIONS, ACCESS_LABELS } from '../periodLabels'
import type { DeviceAccessMode } from '../types'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(
  defineProps<{
    access: DeviceAccessMode
    changedAt?: string | null
    /** 変えた人の表示名。null は「—」(属性の無い既存の行) */
    changedBy?: string | null
    loading?: boolean
    error?: string | null
  }>(),
  { changedAt: null, changedBy: null, loading: false, error: null },
)

const emit = defineEmits<{ submit: [access: DeviceAccessMode] }>()

const CHOICES: DeviceAccessMode[] = ['everyone', 'assigned']
const choice = ref<DeviceAccessMode>(props.access)

watch(
  open,
  (value) => {
    if (value) {
      choice.value = props.access
    }
  },
  { immediate: true },
)

function submit(): void {
  if (props.loading || choice.value === props.access) {
    return
  }
  emit('submit', choice.value)
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
        <v-card-title>通信機ログインに進める人を変える</v-card-title>
        <v-card-subtitle>
          今は「{{ ACCESS_LABELS[access] }}」。変えた日時 {{ changedAt ? formatJst(changedAt) : DASH }}・変えた人
          <span data-test="access-changed-by">{{ changedBy ?? DASH }}</span>
        </v-card-subtitle>
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
        <v-btn-toggle
          v-model="choice"
          mandatory
          density="compact"
          variant="outlined"
          color="primary"
          :disabled="loading"
          class="mb-3"
        >
          <v-btn
            v-for="item in CHOICES"
            :key="item"
            :value="item"
            :data-test="`access-${item}`"
          >
            {{ ACCESS_LABELS[item] }}
          </v-btn>
        </v-btn-toggle>
        <dl class="text-body-2">
          <template
            v-for="item in CHOICES"
            :key="item"
          >
            <dt class="font-weight-medium">
              {{ ACCESS_LABELS[item] }}
            </dt>
            <dd class="mb-2 ms-4">
              {{ ACCESS_DESCRIPTIONS[item] }}
            </dd>
          </template>
        </dl>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="access-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="access-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          :disabled="loading || choice === access"
          @click="submit"
        >
          変える
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
