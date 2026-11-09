<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { SubmitEventPromise } from 'vuetify'
import ResendCodeButton from '@/components/ResendCodeButton.vue'
import { useAuthStore, type Pending, type SignInFactor } from '@/stores/auth'
import { usePasskeysStore } from '@/stores/passkeys'
import { AUTH_SESSION_MINUTES } from '@/utils/authConstants'
import { isSignInSessionError, toJaMessage } from '@/utils/authErrors'
import { readInvitedEmail } from '@/utils/invitedEmail'
import { safeRedirect } from '@/utils/redirect'

/** 前回のメールアドレス。パスキーの前にユーザー名が要るので覚えておく (ADR D1) */
const LAST_EMAIL_KEY = 'service-platform:last-email'
const SESSION_FAILED = 'サインインは完了しましたが、セッションを取得できませんでした。もう一度お試しください'

const FACTOR_LABELS: Record<SignInFactor, { label: string; icon: string }> = {
  WEB_AUTHN: { label: 'パスキーでサインイン', icon: 'mdi-fingerprint' },
  EMAIL_OTP: { label: 'メールにコードを送る', icon: 'mdi-email-fast-outline' },
}

const auth = useAuthStore()
const passkeys = usePasskeysStore()
const route = useRoute()
const router = useRouter()

// /login/admin から移ってきたときなど、前の入口の途中の段を持ち越さない (最初の描画より前に捨てる)
auth.cancelSignIn()

const email = ref('')
const code = ref('')
const localError = ref<string | null>(null)
const sentAt = ref<Date | null>(null)
const resendingCode = ref<Extract<Pending, { kind: 'email-code' }> | null>(null)
const timeFormatter = new Intl.DateTimeFormat('ja-JP', { hour: '2-digit', minute: '2-digit' })
const sentTime = computed(() => sentAt.value === null ? '' : timeFormatter.format(sentAt.value))
const deadline = computed(() => sentAt.value === null
  ? ''
  : timeFormatter.format(new Date(sentAt.value.getTime() + AUTH_SESSION_MINUTES * 60_000)))
const codeResent = ref(false)
let resendNoticeTimer: ReturnType<typeof setTimeout> | null = null

// Amplify は送り直しの開始時に pending を消すため、通信中の表示だけ元のコードの段を保つ。
const currentPending = computed(() => resendingCode.value ?? auth.pending)
const step = computed(() => currentPending.value?.kind ?? 'email')
const message = computed(() => localError.value ?? auth.error)
const offerEmailCode = computed(() => !auth.passkeyFailed || auth.availableFactors === null || auth.availableFactors.includes('EMAIL_OTP'))

const factors = computed<SignInFactor[]>(() => {
  const current = auth.pending
  if (current?.kind !== 'select-factor') {
    return []
  }
  return (['WEB_AUTHN', 'EMAIL_OTP'] as const).filter((factor) => current.factors.includes(factor))
})

const destination = computed(() => {
  const current = currentPending.value
  return current?.kind === 'email-code' && current.destination ? current.destination : null
})

const emailRules = [
  (value: string) => !!value || 'メールアドレスを入力してください',
  (value: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value) || 'メールアドレスの形式が正しくありません',
]

const codeRules = [
  (value: string) => !!value || 'コードを入力してください',
  (value: string) => /^\d{6,8}$/.test(value) || 'メールに書かれた数字のコードを入力してください',
]

function readLastEmail(): string {
  try {
    return localStorage.getItem(LAST_EMAIL_KEY) ?? ''
  } catch {
    return ''
  }
}

function rememberEmail(value: string): void {
  try {
    localStorage.setItem(LAST_EMAIL_KEY, value)
  } catch {
    return
  }
}

onMounted(async () => {
  email.value = readLastEmail()
  const invitedEmail = await readInvitedEmail(route, router, emailRules)
  if (invitedEmail !== null) {
    email.value = invitedEmail
  }
})

function clearResendNotice(): void {
  if (resendNoticeTimer !== null) {
    clearTimeout(resendNoticeTimer)
    resendNoticeTimer = null
  }
  codeResent.value = false
}

watch(
  currentPending,
  (current) => {
    if (current?.kind === 'email-code') {
      sentAt.value = new Date()
    } else {
      sentAt.value = null
      clearResendNotice()
    }
  },
)

onUnmounted(clearResendNotice)

function dismiss(): void {
  auth.error = null
  localError.value = null
}

function restart(): void {
  auth.cancelSignIn()
  localError.value = null
  code.value = ''
  clearResendNotice()
}

/** メールのコードで入った利用者にパスキーが無ければ、アカウントの画面で登録を促す (ADR D1) */
async function needsPasskey(): Promise<boolean> {
  if (auth.lastFactor !== 'EMAIL_OTP' || auth.isAdmin) {
    return false
  }
  await passkeys.fetch()
  return passkeys.loaded && passkeys.items.length === 0
}

/** サインインの段はチャレンジ待ちでも解決するので、`pending` が残っていれば次の段に進むだけにする */
async function proceed(): Promise<void> {
  if (auth.pending !== null) {
    return
  }
  if (!auth.isAuthenticated) {
    localError.value = SESSION_FAILED
    return
  }
  rememberEmail(email.value)
  code.value = ''
  if (await needsPasskey()) {
    await router.push({ name: 'account' })
    return
  }
  await router.push(safeRedirect(route.query.redirect))
}

async function attempt(action: () => Promise<void>): Promise<boolean> {
  localError.value = null
  try {
    await action()
  } catch (error) {
    if (isSignInSessionError(error)) {
      restart()
      localError.value = toJaMessage(error, { passwordlessSignIn: true })
    }
    return false
  }
  await proceed()
  return true
}

async function submitEmail(event: SubmitEventPromise): Promise<void> {
  const { valid } = await event
  if (valid) {
    await attempt(() => auth.startSignIn(email.value))
  }
}

async function startWithEmailCode(): Promise<void> {
  const valid = emailRules.every((rule) => rule(email.value) === true)
  if (!valid) {
    localError.value = 'メールアドレスを正しく入力してください'
    return
  }
  await attempt(() => auth.startSignIn(email.value, 'EMAIL_OTP'))
}

async function choose(factor: SignInFactor): Promise<void> {
  await attempt(() => auth.selectFactor(factor))
}

async function submitCode(event: SubmitEventPromise): Promise<void> {
  if (resendingCode.value !== null) {
    return
  }
  const { valid } = await event
  if (valid && resendingCode.value === null) {
    await attempt(() => auth.submitEmailCode(code.value))
  }
}

async function resend(): Promise<void> {
  if (auth.pending?.kind !== 'email-code' || resendingCode.value !== null) {
    return
  }
  resendingCode.value = auth.pending
  code.value = ''
  clearResendNotice()
  const succeeded = await attempt(() => auth.resendEmailCode())
  resendingCode.value = null
  if (!succeeded && auth.pending === null) {
    const reason = auth.error ?? localError.value
    localError.value = [
      '送り直しに失敗したため、最初の画面に戻りました。',
      reason
        ? `${reason.replace(/。$/, '')}。`
        : 'しばらくしてから、もう一度コードを送ってください。',
    ].join('')
  }
  if (succeeded && auth.pending?.kind === 'email-code') {
    codeResent.value = true
    resendNoticeTimer = setTimeout(() => {
      codeResent.value = false
      resendNoticeTimer = null
    }, 5000)
  }
}
</script>

<template>
  <div>
    <v-alert
      v-if="message"
      type="error"
      variant="tonal"
      closable
      class="mb-4"
      @click:close="dismiss"
    >
      {{ message }}
      <div
        v-if="auth.passkeyFailed && step === 'email'"
        class="mt-2"
      >
        <v-btn
          v-if="offerEmailCode"
          data-test="fallback-email-code"
          variant="tonal"
          size="small"
          prepend-icon="mdi-email-fast-outline"
          :loading="auth.isLoading"
          @click="startWithEmailCode"
        >
          メールのコードでサインイン
        </v-btn>
      </div>
      <div
        v-if="auth.passkeyFailed && !offerEmailCode"
        data-test="fallback-admin"
        class="mt-2"
      >
        管理者の方は
        <RouterLink :to="{ name: 'login-admin' }">
          管理者のサインイン
        </RouterLink>
        からパスワードと認証アプリで入れます。
      </div>
    </v-alert>

    <v-window :model-value="step">
      <v-window-item value="email">
        <v-form @submit.prevent="submitEmail">
          <p class="text-body-2 mb-4">
            メールアドレスを入力し、パスキー (指紋・顔の認証や PIN) でサインインしてください。
          </p>
          <v-text-field
            v-model="email"
            label="メールアドレス"
            type="email"
            autocomplete="username"
            :rules="emailRules"
            prepend-inner-icon="mdi-email-outline"
            class="mb-4"
          />
          <v-btn
            data-test="start-passkey"
            type="submit"
            color="primary"
            block
            size="large"
            prepend-icon="mdi-fingerprint"
            :loading="auth.isLoading"
          >
            パスキーでサインイン
          </v-btn>
          <v-btn
            v-if="offerEmailCode"
            data-test="start-email-code"
            variant="text"
            block
            class="mt-2"
            prepend-icon="mdi-email-fast-outline"
            :disabled="auth.isLoading"
            @click="startWithEmailCode"
          >
            パスキーを使わずにメールのコードでサインイン
          </v-btn>
          <p
            v-if="offerEmailCode"
            class="text-caption text-medium-emphasis mt-2 mb-0"
          >
            はじめてのサインインや、パスキーを登録していない端末では、メールのコードをお使いください。
          </p>
        </v-form>
      </v-window-item>

      <v-window-item value="select-factor">
        <v-alert
          v-if="auth.passwordOnly"
          data-test="password-only"
          type="info"
          variant="tonal"
        >
          このアカウントはパスキーとメールのコードでサインインできません。管理者の方は
          <RouterLink :to="{ name: 'login-admin' }">
            管理者のサインイン
          </RouterLink>
          からお入りください。
        </v-alert>
        <template v-else>
          <p class="text-body-2 mb-4">
            サインインの方法を選んでください。
          </p>
          <v-btn
            v-for="factor in factors"
            :key="factor"
            :data-test="`factor-${factor}`"
            color="primary"
            variant="tonal"
            block
            size="large"
            class="mb-2"
            :prepend-icon="FACTOR_LABELS[factor].icon"
            :loading="auth.isLoading"
            @click="choose(factor)"
          >
            {{ FACTOR_LABELS[factor].label }}
          </v-btn>
        </template>
      </v-window-item>

      <v-window-item value="email-code">
        <v-form
          data-test="email-code-form"
          @submit.prevent="submitCode"
        >
          <p class="text-body-2 mb-4">
            {{ sentTime }} に {{ destination ?? 'メールアドレス' }} 宛にコードを送りました。
          </p>
          <ul class="text-body-2 pl-5 mb-4">
            <li>この画面を閉じずに、別のアプリでメールを見てください。閉じるとコードが無効になり、やり直しになります。</li>
            <li>メールが届くまで数分かかることがあります。届かないときは迷惑メールのフォルダもご確認ください。</li>
            <li>コードを送り直すと、前のコードは使えなくなります。いちばん新しいメールのコードを入れてください。</li>
            <li>{{ deadline }} までに入れてください。過ぎたら最初からやり直してください。</li>
          </ul>
          <p
            v-if="codeResent"
            role="status"
            class="text-body-2 text-success mb-4"
          >
            新しいコードを送りました
          </p>
          <v-text-field
            v-model="code"
            label="確認コード"
            autocomplete="one-time-code"
            inputmode="numeric"
            maxlength="8"
            :rules="codeRules"
            :disabled="resendingCode !== null"
            prepend-inner-icon="mdi-email-check-outline"
            class="mb-4"
          />
          <v-btn
            type="submit"
            color="primary"
            block
            size="large"
            :disabled="resendingCode !== null"
            :loading="auth.isLoading && resendingCode === null"
          >
            サインイン
          </v-btn>
          <ResendCodeButton
            class="mt-2"
            label="コードを送り直す"
            :loading="auth.isLoading"
            @resend="resend"
          />
        </v-form>
      </v-window-item>

      <v-window-item value="totp-code">
        <v-alert
          type="info"
          variant="tonal"
        >
          管理者の方は
          <RouterLink :to="{ name: 'login-admin' }">
            管理者のサインイン
          </RouterLink>
          からお入りください。
        </v-alert>
      </v-window-item>

      <v-window-item value="new-password">
        <v-alert
          type="info"
          variant="tonal"
        >
          管理者の方は
          <RouterLink :to="{ name: 'login-admin' }">
            管理者のサインイン
          </RouterLink>
          からお入りください。
        </v-alert>
      </v-window-item>
    </v-window>

    <div
      v-if="step === 'email'"
      class="text-center text-body-2 mt-6"
    >
      <RouterLink
        data-test="admin-login-link"
        :to="{ name: 'login-admin' }"
        class="text-medium-emphasis"
      >
        管理者の方はこちら
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
        :disabled="auth.isLoading"
        @click="restart"
      >
        {{ step === 'email-code' ? 'メールアドレスを直す' : '最初からやり直す' }}
      </v-btn>
    </div>
  </div>
</template>
