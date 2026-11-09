<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { REASON_MAX, maxRule } from '../labels'

const open = defineModel<boolean>({ required: true })

withDefaults(defineProps<{ email: string | null; loading?: boolean }>(), { loading: false })

const emit = defineEmits<{ submit: [reason: string | null] }>()

const form = ref<VForm | null>(null)
const reason = ref('')

watch(open, (value) => value && (reason.value = ''), { immediate: true })

async function submit(): Promise<void> {
  const result = await form.value?.validate()
  if (result?.valid) {
    emit('submit', reason.value.trim() || null)
  }
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
        <v-card-title>利用を停止しますか?</v-card-title>
        <v-card-subtitle>{{ email }}</v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <v-alert
          type="warning"
          variant="tonal"
          density="compact"
          class="mb-4"
        >
          台帳で停止し、Cognito で無効にして、全てのセッションを失効させます。発行済みのトークンが残っていても SERVICE-PLATFORM の API はすぐに断ります。パスキーは残ります (盗難のときは再招待を使ってください)。
        </v-alert>
        <v-form
          ref="form"
          @submit.prevent="submit"
        >
          <v-textarea
            v-model="reason"
            hint="理由は監査に 365 日残ります。暗証番号・連絡先・パスワードを書かないでください (500 文字まで)。"
            persistent-hint
            data-test="suspend-reason"
            label="理由 (任意)"
            rows="2"
            auto-grow
            :rules="[maxRule(REASON_MAX)]"
          />
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="suspend-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="suspend-submit"
          color="warning"
          variant="flat"
          :loading="loading"
          @click="submit"
        >
          停止する
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
