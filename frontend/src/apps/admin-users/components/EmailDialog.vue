<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { emailRule } from '../labels'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(defineProps<{ current: string | null; loading?: boolean }>(), {
  loading: false,
})

const emit = defineEmits<{ submit: [email: string] }>()

const form = ref<VForm | null>(null)
const email = ref('')

const rules = [
  emailRule,
  (value: string) =>
    value.trim().toLowerCase() !== (props.current ?? '').toLowerCase() || '今と同じメールアドレスです',
]

watch(open, (value) => value && (email.value = ''), { immediate: true })

async function submit(): Promise<void> {
  const result = await form.value?.validate()
  if (result?.valid) {
    emit('submit', email.value.trim())
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
        <v-card-title>メールアドレスを変える</v-card-title>
        <v-card-subtitle>今: {{ current }}</v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <v-alert
          type="info"
          variant="tonal"
          density="compact"
          class="mb-4"
        >
          本人からの申し出は、メールではなく別経路 (台帳の連絡先への電話など) で確かめてから変えてください。乗っ取った者に宛先を移されないためです。変えたことは監査に残ります。
        </v-alert>
        <v-form
          ref="form"
          @submit.prevent="submit"
        >
          <v-text-field
            v-model="email"
            data-test="email-new"
            label="新しいメールアドレス"
            type="email"
            :rules="rules"
          />
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="email-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="email-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          @click="submit"
        >
          変える
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
