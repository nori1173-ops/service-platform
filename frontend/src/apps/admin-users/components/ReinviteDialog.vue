<script setup lang="ts">
import { ref, watch } from 'vue'
import type { VForm } from 'vuetify/components'
import { NOTE_MAX, REASON_MAX, VERIFY_METHODS, VERIFY_METHOD_LABELS, emailRule, maxRule } from '../labels'
import type { ReinviteInput, UserKind, UserRole, VerifyMethod } from '../types'

const open = defineModel<boolean>({ required: true })

const props = withDefaults(
  defineProps<{ email: string | null; isAdmin: boolean; kind?: UserKind; loading?: boolean }>(),
  { loading: false, kind: 'employee' },
)

const emit = defineEmits<{ submit: [input: ReinviteInput] }>()

const form = ref<VForm | null>(null)
const reason = ref('')
const role = ref<UserRole>('user')
const newEmail = ref('')
const method = ref<VerifyMethod | null>(null)
const verifiedNote = ref('')

const reasonRules = [
  (value: string) => !!value.trim() || '理由を入力してください',
  maxRule(REASON_MAX),
]
const newEmailRules = [(value: string) => !value.trim() || emailRule(value)]

function reset(): void {
  reason.value = ''
  role.value = props.isAdmin ? 'admin' : 'user'
  newEmail.value = ''
  method.value = null
  verifiedNote.value = ''
}

watch(open, (value) => value && reset(), { immediate: true })

async function submit(): Promise<void> {
  const result = await form.value?.validate()
  if (!result?.valid) {
    return
  }
  const input: ReinviteInput = { reason: reason.value.trim(), role: role.value }
  const address = newEmail.value.trim()
  if (address && address.toLowerCase() !== (props.email ?? '').toLowerCase()) {
    input.email = address
  }
  if (method.value) {
    const note = verifiedNote.value.trim()
    input.verified_before = note ? { method: method.value, note } : { method: method.value }
  }
  emit('submit', input)
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
        <v-card-title>再招待しますか?</v-card-title>
        <v-card-subtitle>{{ email }}</v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <v-alert
          type="warning"
          variant="tonal"
          density="compact"
          class="mb-4"
        >
          Cognito の利用者を消して作り直します。パスキーと、後から足されたパスワードは消えます。user_id・割り当て・履歴は残ります。台帳は有効に戻すまで停止のままです。
          <template v-if="role === 'admin'">
            管理者の再招待では招待のメールを送らず、一時パスワードを 1 回だけ表示します。管理者の状態は準備中に戻ります。
          </template>
          <template v-else>
            本人に新しい招待のメールが届きます。メールのコードでサインインしてパスキーを登録してもらい、別経路で確かめてから有効に戻してください。
          </template>
        </v-alert>
        <v-form
          ref="form"
          @submit.prevent="submit"
        >
          <div class="text-body-2 mb-1">
            再招待後の役割
          </div>
          <v-btn-toggle
            v-model="role"
            mandatory
            color="primary"
            variant="outlined"
            class="mb-3"
          >
            <v-btn
              value="user"
              data-test="reinvite-role-user"
            >
              user (一般)
            </v-btn>
            <v-btn
              v-if="kind === 'employee'"
              value="admin"
              data-test="reinvite-role-admin"
            >
              admin
            </v-btn>
          </v-btn-toggle>
          <v-textarea
            v-model="reason"
            hint="理由は監査に 365 日残ります。暗証番号・連絡先・パスワードを書かないでください (500 文字まで)。"
            persistent-hint
            data-test="reinvite-reason"
            label="理由 (盗難、TOTP の紛失など)"
            rows="2"
            auto-grow
            :rules="reasonRules"
            class="mb-3"
          />
          <v-text-field
            v-model="newEmail"
            data-test="reinvite-email"
            label="新しいメールアドレス (宛先を変えるときだけ)"
            type="email"
            :rules="newEmailRules"
            class="mb-1"
          />
          <p class="text-caption text-medium-emphasis mb-3">
            受信箱の乗っ取りが疑われるときは、同じ宛先へそのまま再招待せず、受信箱の安全を確かめるか宛先を変えてください。
          </p>
          <div class="text-body-2 mb-1">
            再招待の前の別経路の本人確認 (記録するとき)
          </div>
          <v-btn-toggle
            v-model="method"
            color="primary"
            variant="outlined"
            density="comfortable"
            class="mb-3"
          >
            <v-btn
              v-for="value in VERIFY_METHODS"
              :key="value"
              :value="value"
              :data-test="`reinvite-method-${value}`"
            >
              {{ VERIFY_METHOD_LABELS[value] }}
            </v-btn>
          </v-btn-toggle>
          <v-textarea
            v-if="method"
            v-model="verifiedNote"
            data-test="reinvite-verified-note"
            label="確認のメモ"
            rows="2"
            auto-grow
            :rules="[maxRule(NOTE_MAX)]"
          />
        </v-form>
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn
          data-test="reinvite-cancel"
          variant="text"
          :disabled="loading"
          @click="open = false"
        >
          キャンセル
        </v-btn>
        <v-btn
          data-test="reinvite-submit"
          color="warning"
          variant="flat"
          :loading="loading"
          @click="submit"
        >
          再招待する
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>
