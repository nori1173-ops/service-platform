<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { CONTACT_MAX, NOTE_MAX, ORG_NAME_MAX, emailRule, maxRule } from '../labels'
import type { InviteInput, UserKind } from '../types'

const open = defineModel<boolean>({ required: true })

withDefaults(defineProps<{ loading?: boolean; error?: string | null }>(), {
  loading: false,
  error: null,
})

const emit = defineEmits<{ submit: [input: InviteInput] }>()

const form = ref<VForm | null>(null)
const email = ref('')
const kind = ref<UserKind>('customer')
const asAdmin = ref(false)
const orgName = ref('')
const contact = ref('')
const note = ref('')

const orgRules = [
  (value: string) => kind.value !== 'customer' || !!value.trim() || 'お客様は組織名が要ります',
  maxRule(ORG_NAME_MAX),
]

function reset(): void {
  email.value = ''
  kind.value = 'customer'
  asAdmin.value = false
  orgName.value = ''
  contact.value = ''
  note.value = ''
}

watch(open, (value) => value && reset(), { immediate: true })

// admin は社員で招待する (ADR D4。API も 400 で断る)
watch(kind, (value) => {
  if (value !== 'employee') {
    asAdmin.value = false
  }
})

async function submit(): Promise<void> {
  const result = await form.value?.validate()
  if (!result?.valid) {
    return
  }
  const input: InviteInput = {
    email: email.value.trim(),
    kind: kind.value,
    role: asAdmin.value ? 'admin' : 'user',
  }
  const optional = { org_name: orgName.value, contact: contact.value, note: note.value }
  for (const [key, value] of Object.entries(optional)) {
    if (value.trim()) {
      input[key as keyof typeof optional] = value.trim()
    }
  }
  emit('submit', input)
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
        <v-card-title>利用者を招待する</v-card-title>
      </v-card-item>
      <v-card-text>
        <v-alert
          v-if="error"
          data-test="invite-error"
          type="error"
          variant="tonal"
          class="mb-4"
        >
          {{ error }}
        </v-alert>
        <v-form
          ref="form"
          @submit.prevent="submit"
        >
          <v-text-field
            v-model="email"
            data-test="invite-email"
            label="メールアドレス"
            type="email"
            :rules="[emailRule]"
            class="mb-3"
          />
          <div class="text-body-2 mb-1">
            種別
          </div>
          <v-btn-toggle
            v-model="kind"
            mandatory
            color="primary"
            variant="outlined"
            density="comfortable"
            class="mb-2"
          >
            <v-btn
              value="customer"
              data-test="invite-kind-customer"
            >
              お客様
            </v-btn>
            <v-btn
              value="employee"
              data-test="invite-kind-employee"
            >
              社員
            </v-btn>
          </v-btn-toggle>
          <v-checkbox
            v-model="asAdmin"
            data-test="invite-admin"
            label="管理者 (admin) として招待する"
            :disabled="kind !== 'employee'"
            hide-details
            density="compact"
            class="mb-3"
          />
          <v-text-field
            v-model="orgName"
            data-test="invite-org"
            label="組織名"
            :rules="orgRules"
            class="mb-3"
          />
          <v-text-field
            v-model="contact"
            data-test="invite-contact"
            label="連絡先 (別経路の本人確認に使う。組織の窓口の電話など)"
            :rules="[maxRule(CONTACT_MAX)]"
            class="mb-3"
          />
          <v-textarea
            v-model="note"
            data-test="invite-note"
            label="メモ"
            rows="2"
            auto-grow
            :rules="[maxRule(NOTE_MAX)]"
          />
        </v-form>
        <p class="text-caption text-medium-emphasis mt-3 mb-0">
          お客様と社員には招待のメールが届き、メールのコードでサインインしてパスキーを登録します。管理者には招待のメールを送らず、一時パスワードを 1 回だけ表示します。社内の別経路で本人に渡してください。
        </p>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="invite-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="invite-submit"
          color="primary"
          variant="flat"
          :loading="loading"
          @click="submit"
        >
          招待する
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
