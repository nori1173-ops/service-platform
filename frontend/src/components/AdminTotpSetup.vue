<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import TotpEnroll from '@/components/TotpEnroll.vue'
import { useAuthStore, type TotpSetup } from '@/stores/auth'
import { useMeStore } from '@/stores/me'

type Phase = 'checking' | 'start' | 'enroll' | 'notify'

const auth = useAuthStore()
const me = useMeStore()
const router = useRouter()

const phase = ref<Phase>('checking')
// 共有の秘密はこの画面だけが持ち、済んだら消す
const setup = ref<TotpSetup | null>(null)
const working = ref(false)
const localError = ref<string | null>(null)

onMounted(async () => {
  try {
    phase.value = (await auth.totpEnabled()) ? 'notify' : 'start'
  } catch {
    phase.value = 'start'
  }
})

onUnmounted(() => {
  setup.value = null
})

async function start(): Promise<void> {
  localError.value = null
  try {
    setup.value = await auth.beginTotpSetup()
    phase.value = 'enroll'
  } catch {
    localError.value = auth.error
  }
}

/** 「TOTP の登録を終えた」API (ADR D9 の例外 1)。サーバはそれまでのセッションを失効させるのでサインアウトする */
async function notify(): Promise<void> {
  working.value = true
  localError.value = null
  try {
    await me.registerTotp()
  } catch (e: unknown) {
    localError.value = e instanceof Error ? e.message : String(e)
    phase.value = 'notify'
    working.value = false
    return
  }
  setup.value = null
  await auth.logout()
  working.value = false
  await router.push({ name: 'login-admin', query: { registered: '1' } })
}

async function enroll(code: string): Promise<void> {
  localError.value = null
  try {
    await auth.confirmTotpSetup(code)
  } catch {
    localError.value = auth.error
    return
  }
  setup.value = null
  await notify()
}
</script>

<template>
  <v-card
    variant="flat"
    border
  >
    <v-card-item>
      <v-card-title>認証アプリ (TOTP) の登録</v-card-title>
      <v-card-subtitle>管理者はパスワードと認証アプリのコードでサインインします。</v-card-subtitle>
    </v-card-item>
    <v-card-text>
      <v-alert
        v-if="localError"
        data-test="totp-error"
        type="error"
        variant="tonal"
        class="mb-4"
      >
        {{ localError }}
      </v-alert>

      <template v-if="phase === 'start'">
        <p class="text-body-2 mb-4">
          スマホなどの認証アプリ (Google Authenticator など) を用意してから始めてください。登録を終えると、確認待ちになってサインアウトします。
        </p>
        <v-btn
          data-test="totp-start"
          color="primary"
          prepend-icon="mdi-shield-key-outline"
          :loading="auth.isLoading"
          @click="start"
        >
          認証アプリを登録する
        </v-btn>
      </template>

      <TotpEnroll
        v-else-if="phase === 'enroll' && setup"
        :setup-uri="setup.setupUri"
        :shared-secret="setup.sharedSecret"
        :loading="auth.isLoading || working"
        @submit="enroll"
      />

      <template v-else-if="phase === 'notify'">
        <p class="text-body-2 mb-4">
          認証アプリの登録は済んでいます。「登録を終えた」ことを知らせると確認待ちになり、サインアウトします。
        </p>
        <v-btn
          data-test="totp-notify"
          color="primary"
          prepend-icon="mdi-send-check-outline"
          :loading="working"
          @click="notify"
        >
          登録を終えたことを知らせる
        </v-btn>
      </template>
    </v-card-text>
  </v-card>
</template>
