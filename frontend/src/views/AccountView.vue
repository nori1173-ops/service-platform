<script setup lang="ts">
import { computed, onActivated } from 'vue'
import { useRouter } from 'vue-router'
import AccountStatusBanner from '@/components/AccountStatusBanner.vue'
import AdminTotpSetup from '@/components/AdminTotpSetup.vue'
import AssignmentList from '@/components/AssignmentList.vue'
import PasskeyManager from '@/components/PasskeyManager.vue'
import { useAuthStore } from '@/stores/auth'
import { useMeStore } from '@/stores/me'
import { DASH } from '@/utils/format'

const KIND_LABELS: Record<string, string> = { customer: 'お客様', employee: '社員' }

const auth = useAuthStore()
const meStore = useMeStore()
const router = useRouter()

const me = computed(() => meStore.me)
const isAdmin = computed(() => me.value?.is_admin ?? false)
const kindLabel = computed(() => (me.value?.kind ? KIND_LABELS[me.value.kind] : DASH))
// 割り当ての一覧の API は有効な利用者だけが呼べる (停止中・admin の準備中 / 確認待ちは出さない)
const showAssignments = computed(
  () => me.value?.status === 'active' && (!isAdmin.value || me.value.admin_state === 'active'),
)

// keep-alive 配下では onMounted が再発火しないため、画面に入るたびに状態を読み直す (有効化の後など)
onActivated(() => meStore.load())

async function signOut(): Promise<void> {
  const target = isAdmin.value ? 'login-admin' : 'login'
  try {
    await auth.logout()
  } finally {
    await router.push({ name: target })
  }
}
</script>

<template>
  <div>
    <div class="d-flex align-center flex-wrap ga-3 mb-4">
      <h1 class="text-h5 mb-0">
        アカウント
      </h1>
      <v-spacer />
      <v-btn
        data-test="account-signout"
        variant="tonal"
        prepend-icon="mdi-logout"
        @click="signOut"
      >
        サインアウト
      </v-btn>
    </div>

    <AccountStatusBanner
      :me="me"
      :error="meStore.error"
      :denied="meStore.denied"
      @retry="meStore.load()"
    />

    <v-card
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-item>
        <v-card-title>サインインの情報</v-card-title>
      </v-card-item>
      <v-card-text>
        <v-table density="compact">
          <tbody>
            <tr>
              <th class="text-no-wrap">
                メールアドレス
              </th>
              <td>{{ me?.email ?? auth.email ?? DASH }}</td>
            </tr>
            <tr>
              <th class="text-no-wrap">
                種別
              </th>
              <td>{{ kindLabel }}</td>
            </tr>
            <tr>
              <th class="text-no-wrap">
                組織
              </th>
              <td>{{ me?.org_name ?? DASH }}</td>
            </tr>
            <tr v-if="isAdmin">
              <th class="text-no-wrap">
                管理者
              </th>
              <td>
                {{ me?.admin_state === 'active' ? '有効' : me?.admin_state === 'awaiting' ? '確認待ち' : '準備中' }}
              </td>
            </tr>
          </tbody>
        </v-table>
        <p class="text-caption text-medium-emphasis mt-3 mb-0">
          メールアドレスを変えるときは管理者へ依頼してください。共用の端末では、使い終えたらサインアウトしてください。
        </p>
      </v-card-text>
    </v-card>

    <template v-if="me">
      <AssignmentList v-if="showAssignments" />
      <AdminTotpSetup v-if="isAdmin && me.admin_state === 'pending'" />
      <template v-else-if="!isAdmin || me.admin_state === 'active'">
        <p
          v-if="isAdmin"
          class="text-body-2 mb-4"
        >
          パスキーを登録すると、通常のサインインからパスキーでも入れます。パスワードと認証アプリの入口も残ります。
        </p>
        <PasskeyManager :admin="isAdmin" />
      </template>
    </template>
  </div>
</template>
