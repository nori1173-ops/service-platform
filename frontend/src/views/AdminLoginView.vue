<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { SubmitEventPromise } from 'vuetify'
import { useAuthStore } from '@/stores/auth'
import { passwordPolicyRule } from '@/utils/password'
import { readInvitedEmail } from '@/utils/invitedEmail'
import { safeRedirect } from '@/utils/redirect'

const SESSION_FAILED = 'サインインは完了しましたが、セッションを取得できませんでした。もう一度お試しください'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()

// /login から移ってきたときなど、前の入口の途中の段を持ち越さない (最初の描画より前に捨てる)
auth.cancelSignIn()

const email = ref('')
// パスワードは画面のメモリだけで使い、送ったら消す
const password = ref('')
const showPassword = ref(false)
const totpCode = ref('')
const newPassword = ref('')
const newPasswordConfirm = ref('')
const localError = ref<string | null>(null)

const step = computed(() => {
  const kind = auth.pending?.kind
  if (kind === 'select-factor' || kind === 'email-code') {
    return 'not-password-user'
  }
  return kind ?? 'password'
})
const message = computed(() => auth.error ?? localError.value)
const registered = computed(() => route.query.registered === '1')

const emailRules = [
  (value: string) => !!value || 'メールアドレスを入力してください',
  (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value) || 'メールアドレスの形式が正しくありません',
]
const passwordRules = [(value: string) => !!value || 'パスワードを入力してください']
const newPasswordRules = [
  (value: string) => !!value || '新しいパスワードを入力してください',
  passwordPolicyRule,
]
const confirmRules = [
  (value: string) => value === newPassword.value || '同じパスワードをもう一度入力してください',
]
const codeRules = [
  (value: string) => !!value || '認証コードを入力してください',
  (value: string) => /^\d{6}$/.test(value) || '6 桁の数字を入力してください',
]

onMounted(async () => {
  const invitedEmail = await readInvitedEmail(route, router, emailRules)
  if (invitedEmail !== null) {
    email.value = invitedEmail
  }
})

function clearSecrets(): void {
  password.value = ''
  newPassword.value = ''
  newPasswordConfirm.value = ''
  totpCode.value = ''
}

function dismiss(): void {
  auth.error = null
  localError.value = null
}

function restart(): void {
  auth.cancelSignIn()
  localError.value = null
  clearSecrets()
}

async function proceed(): Promise<void> {
  if (auth.pending !== null) {
    return
  }
  if (!auth.isAuthenticated) {
    localError.value = SESSION_FAILED
    return
  }
  await router.push(safeRedirect(route.query.redirect))
}

async function attempt(action: () => Promise<void>): Promise<void> {
  localError.value = null
  try {
    await action()
  } catch {
    return
  } finally {
    clearSecrets()
  }
  await proceed()
}

async function submitPassword(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (valid) {
    const entered = password.value
    await attempt(() => auth.startAdminSignIn(email.value, entered))
  }
}

async function submitTotp(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (valid) {
    const entered = totpCode.value
    await attempt(() => auth.submitTotp(entered))
  }
}

async function submitNewPassword(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (valid) {
    const entered = newPassword.value
    await attempt(() => auth.submitNewPassword(entered))
  }
}
</script>

<template>
  <div>
    <div class="text-subtitle-1 font-weight-medium text-center mb-4">
      管理者のサインイン
    </div>

    <v-alert
      v-if="registered && step === 'password'"
      data-test="registered-notice"
      type="success"
      variant="tonal"
      class="mb-4"
    >
      認証アプリ (TOTP) の登録を受け付けました。確認待ちです。別の管理者 (または運用者) が社内の別経路であなたの登録を確かめて有効にした後、パスワードと認証アプリのコードでサインインし直してください。
    </v-alert>

    <v-alert
      v-if="message"
      type="error"
      variant="tonal"
      closable
      class="mb-4"
      @click:close="dismiss"
    >
      {{ message }}
    </v-alert>

    <p
      data-test="passkey-login-guide"
      class="text-body-2 mb-4"
    >
      パスキーを登録済みの方は
      <RouterLink :to="{ name: 'login' }">
        通常のサインイン
      </RouterLink>
      からも入れます。
    </p>
    <v-window :model-value="step">
      <v-window-item value="password">
        <v-form
          data-test="password-form"
          @submit.prevent="submitPassword"
        >
          <v-text-field
            v-model="email"
            label="メールアドレス"
            type="email"
            autocomplete="username"
            :rules="emailRules"
            prepend-inner-icon="mdi-email-outline"
            class="mb-4"
          />
          <v-text-field
            v-model="password"
            label="パスワード"
            :type="showPassword ? 'text' : 'password'"
            autocomplete="current-password"
            :rules="passwordRules"
            prepend-inner-icon="mdi-lock-outline"
            :append-inner-icon="showPassword ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
            class="mb-4"
            @click:append-inner="showPassword = !showPassword"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
          >
            サインイン
          </v-btn>
          <p class="text-caption text-medium-emphasis mt-3 mb-0">
            パスワードを忘れたとき・変えたいときは、別の管理者に一時パスワードの発行を依頼してください。
          </p>
        </v-form>
      </v-window-item>

      <v-window-item value="totp-code">
        <v-form
          data-test="totp-form"
          @submit.prevent="submitTotp"
        >
          <p class="text-body-2 mb-4">
            認証アプリに表示されている 6 桁のコードを入力してください。
          </p>
          <v-text-field
            v-model="totpCode"
            label="認証コード（6 桁）"
            autocomplete="one-time-code"
            inputmode="numeric"
            maxlength="6"
            :rules="codeRules"
            prepend-inner-icon="mdi-shield-key-outline"
            class="mb-4"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
          >
            コードを確認
          </v-btn>
        </v-form>
      </v-window-item>

      <v-window-item value="new-password">
        <v-form
          data-test="new-password-form"
          @submit.prevent="submitNewPassword"
        >
          <p class="text-body-2 mb-4">
            一時パスワードでサインインしました。新しいパスワードを決めてください。
          </p>
          <v-text-field
            v-model="newPassword"
            label="新しいパスワード"
            type="password"
            autocomplete="new-password"
            :rules="newPasswordRules"
            prepend-inner-icon="mdi-lock-reset"
            class="mb-4"
          />
          <v-text-field
            v-model="newPasswordConfirm"
            label="新しいパスワード（確認）"
            type="password"
            autocomplete="new-password"
            :rules="confirmRules"
            prepend-inner-icon="mdi-lock-check-outline"
            class="mb-4"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :loading="auth.isLoading"
          >
            パスワードを決める
          </v-btn>
        </v-form>
      </v-window-item>

      <v-window-item value="not-password-user">
        <v-alert
          data-test="not-password-user"
          type="info"
          variant="tonal"
        >
          このアカウントはパスワードでサインインできません。
          <RouterLink :to="{ name: 'login' }">
            通常のサインイン
          </RouterLink>
          からお入りください。
        </v-alert>
      </v-window-item>
    </v-window>

    <div
      v-if="step === 'password'"
      class="text-center text-body-2 mt-6"
    >
      <RouterLink
        data-test="customer-login-link"
        :to="{ name: 'login' }"
        class="text-medium-emphasis"
      >
        通常のサインインへ戻る
      </RouterLink>
    </div>
    <div
      v-else
      class="text-center mt-6"
    >
      <v-btn
        data-test="restart"
        variant="text"
        size="small"
        prepend-icon="mdi-arrow-left"
        @click="restart"
      >
        最初からやり直す
      </v-btn>
    </div>
  </div>
</template>
