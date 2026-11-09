<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { launchTarget, shortcutDevice } from '@/router/landing'
import { MESSAGES, useDeviceStore } from '@/stores/device'
import { useLandingStore } from '@/stores/landing'
import type { MyDevice } from '@/types/devices'
import { DASH } from '@/utils/format'
import { leaveTo } from '@/utils/navigation'

const device = useDeviceStore()
const landing = useLandingStore()
const router = useRouter()
/** 着地の近道  で来たときは、一覧の表を出さずに選んだ通信機の暗証番号の段だけを出す */
const shortcut = computed(() => landing.account !== null)
const pinVisible = ref(false)
const filter = ref('')
const FILTER_THRESHOLD = 10

const showFilter = computed(() => device.devices.length > FILTER_THRESHOLD)
function matches(item: MyDevice): boolean {
  const text = filter.value.trim()
  return !showFilter.value || !text || item.account.includes(text) || (item.org_name ?? '').includes(text)
}
const assignedRows = computed(() => device.assignedDevices.filter(matches))
const everyoneRows = computed(() => device.everyoneDevices.filter(matches))

function basisLabel(item: MyDevice): string {
  return item.basis === 'group' ? `グループ: ${item.group_label ?? DASH}` : '本人'
}

const elapsedText = computed(
  () => `${Math.floor(device.elapsedSeconds / 60)} 分 ${device.elapsedSeconds % 60} 秒`,
)

// 書式の誤りは入力があるときだけ出す (空欄はボタンを押せないだけ)
const serialErrors = computed(() => (device.serial && device.errors.serial ? [device.errors.serial] : []))
const pinErrors = computed(() => (device.pin && device.errors.pin ? [device.errors.pin] : []))

async function load(): Promise<void> {
  await device.loadDevices()
  // 読み直した一覧で条件が崩れたら (通信機やサービスが増えたら)、近道をやめて今までの表に戻る
  if (landing.account !== null && shortcutDevice(device.devices, false)?.account !== landing.account) {
    landing.clear()
  }
  const only = device.devices.length === 1 ? device.devices[0] : null
  if (only && device.selected?.account !== only.account) {
    await device.select(only.account)
  }
}

// ログインが成立したら (前回値でも、最新値の待機の後でも) サービスの一覧へ進む。
// 近道で来ていて、許可されたサービスが 1 つなら、サービスの一覧も飛ばしてそのサービスへ (同じタブ、ADR D6)
watch(
  () => device.loggedIn,
  (result) => {
    if (result) {
      const done = device.consumeLogin() ?? result
      const url = landing.account === done.account ? launchTarget(done.services) : null
      if (url) {
        leaveTo(url)
        return
      }
      void router.push({ name: 'device-services', params: { account: done.account } })
    }
  },
)

// 停止・再招待・admin の状態で断られたら、状態を読み直したアカウントの画面へ移る
watch(
  () => device.leaveTo,
  (target) => {
    if (target && device.consumeLeave()) {
      void router.push({ name: target })
    }
  },
)

// keep-alive の中では最初の表示で onMounted と onActivated の両方が来るので、2 回目の表示から読み直す
let mounted = false
onMounted(() => {
  mounted = true
  void load()
})
onActivated(() => {
  if (mounted) {
    mounted = false
    return
  }
  void load()
})
// 画面を離れたら待機を止め、メモリ上の暗証番号を捨てる (keep-alive でも捨てる)
onDeactivated(() => device.dispose())
onBeforeUnmount(() => device.dispose())
</script>

<template>
  <div>
    <h1 class="text-h5 mb-1">
      通信機ログイン
    </h1>
    <p class="text-body-2 text-medium-emphasis mb-6">
      {{ shortcut ? '暗証番号を入力してください。' : '通信機を選び、暗証番号を入力してください。' }}暗証番号は保存しません。
    </p>

    <v-alert
      v-if="shortcut"
      data-test="shortcut-notice"
      type="info"
      variant="tonal"
      density="compact"
      class="mb-4"
    >
      使える通信機が 1 台なので、入り方の選択と通信機の一覧を飛ばしました。
      <template #append>
        <v-btn
          variant="text"
          size="small"
          data-test="shortcut-show-list"
          @click="landing.clear()"
        >
          一覧を出す
        </v-btn>
      </template>
    </v-alert>

    <v-alert
      v-if="device.loadError"
      data-test="assignments-error"
      type="error"
      variant="tonal"
      class="mb-4"
    >
      {{ device.loadError }}
      <template #append>
        <v-btn
          variant="text"
          @click="load()"
        >
          読み直す
        </v-btn>
      </template>
    </v-alert>

    <v-progress-linear
      v-if="device.loading"
      indeterminate
      color="primary"
      class="mb-2"
    />
    <v-alert
      v-if="!device.loading && !device.loadError && device.devices.length === 0"
      data-test="no-assignments"
      type="info"
      variant="tonal"
      class="mb-4"
    >
      使える通信機がありません。使う通信機を管理者に割り当ててもらってください。
    </v-alert>
    <v-text-field
      v-if="showFilter && !shortcut"
      v-model="filter"
      data-test="device-filter"
      label="通信機アカウントか組織名で絞る"
      prepend-inner-icon="mdi-magnify"
      clearable
      density="compact"
      class="mb-2"
      style="max-width: 360px"
    />

    <v-card
      v-if="device.assignedDevices.length && !shortcut"
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-item>
        <v-card-title>割り当てられた通信機</v-card-title>
        <v-card-subtitle>行を選ぶと、その通信機で通信機ログインをします。</v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <v-table density="comfortable">
          <thead>
            <tr>
              <th>通信機アカウント</th>
              <th>組織</th>
              <th>割り当て</th>
              <th>製造番号</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in assignedRows"
              :key="item.account"
              :data-test="`assignment-${item.account}`"
              class="assignment-row"
              :class="{ 'assignment-row--active': item.account === device.selected?.account }"
              tabindex="0"
              role="button"
              :aria-pressed="item.account === device.selected?.account"
              @click="device.select(item.account)"
              @keydown.enter.self="device.select(item.account)"
              @keydown.space.self.prevent="device.select(item.account)"
            >
              <td>{{ item.account }}</td>
              <td>{{ item.org_name ?? DASH }}</td>
              <td>{{ basisLabel(item) }}</td>
              <td>{{ item.serial ?? '未登録 (初回に入力します)' }}</td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>

    <v-card
      v-if="device.everyoneDevices.length && !shortcut"
      variant="flat"
      border
      class="mb-4"
    >
      <v-card-item>
        <v-card-title>誰でも使える通信機</v-card-title>
        <v-card-subtitle>暗証番号を知っている人だけが通信機ログインできます。初回は製造番号も入力します。</v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <v-table density="comfortable">
          <thead>
            <tr>
              <th>通信機アカウント</th>
              <th>組織</th>
              <th>製造番号</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in everyoneRows"
              :key="item.account"
              :data-test="`everyone-${item.account}`"
              class="assignment-row"
              :class="{ 'assignment-row--active': item.account === device.selected?.account }"
              tabindex="0"
              role="button"
              :aria-pressed="item.account === device.selected?.account"
              @click="device.select(item.account)"
              @keydown.enter.self="device.select(item.account)"
              @keydown.space.self.prevent="device.select(item.account)"
            >
              <td>{{ item.account }}</td>
              <td>{{ item.org_name ?? DASH }}</td>
              <td>{{ item.serial ?? '未登録 (初回に入力します)' }}</td>
            </tr>
          </tbody>
        </v-table>
      </v-card-text>
    </v-card>

    <v-card
      v-if="device.selected"
      variant="flat"
      border
    >
      <v-card-item>
        <v-card-title>
          通信機アカウント
          <span data-test="selected-account">{{ device.selected.account }}</span>
        </v-card-title>
        <v-card-subtitle v-if="!device.needsSerial">
          製造番号 {{ device.serial }}
        </v-card-subtitle>
      </v-card-item>
      <v-card-text>
        <div class="login-fields">
          <v-text-field
            v-if="device.needsSerial"
            label="製造番号"
            :model-value="device.serial"
            maxlength="8"
            inputmode="numeric"
            autocomplete="off"
            hint="初回だけ入力します。ログインに成功すると控えます"
            persistent-hint
            :disabled="device.busy"
            :error-messages="serialErrors"
            data-test="field-serial"
            @update:model-value="device.setSerial($event)"
          />
          <v-text-field
            label="暗証番号"
            :model-value="device.pin"
            maxlength="4"
            inputmode="numeric"
            autocomplete="off"
            :type="pinVisible ? 'text' : 'password'"
            :append-inner-icon="pinVisible ? 'mdi-eye-off' : 'mdi-eye'"
            :disabled="device.busy"
            :error-messages="pinErrors"
            data-test="field-pin"
            @click:append-inner="pinVisible = !pinVisible"
            @update:model-value="device.setPin($event)"
          />
        </div>
        <div class="d-flex flex-wrap ga-3 mt-2">
          <v-btn
            color="primary"
            variant="flat"
            :disabled="!device.canLoadPrevious"
            :loading="device.phase === 'previous'"
            data-test="load-previous"
            @click="device.loadPrevious()"
          >
            前回値を読み込む
          </v-btn>
          <v-btn
            color="primary"
            variant="outlined"
            :disabled="!device.canStartLatest"
            :loading="device.phase === 'starting'"
            data-test="start-latest"
            @click="device.openRefreshWarning()"
          >
            最新値を取得
          </v-btn>
        </div>
        <p class="text-caption text-medium-emphasis mt-3 mb-0">
          前回値は通信機が前に報告した設定と照らし合わせて、すぐにログインします。「最新値を取得」は、暗証番号を前回値で確かめてから通信機と通信して設定を読み取り直します (運用中の通信機と、設定が一度も読み取られていない通信機では使えません)。
        </p>
        <p
          v-if="device.refreshBlockedReason"
          class="text-error text-body-2 mt-2 mb-0"
          data-test="refresh-reason"
        >
          {{ device.refreshBlockedReason }}
        </p>
        <div
          v-if="device.phase === 'waiting'"
          class="waiting mt-4"
          data-test="waiting"
        >
          <v-progress-linear
            indeterminate
            color="primary"
          />
          <span>{{ MESSAGES.waiting }} (経過 {{ elapsedText }})</span>
          <v-btn
            variant="outlined"
            color="primary"
            data-test="cancel-wait"
            @click="device.cancelWaiting()"
          >
            待機を中断
          </v-btn>
        </div>
        <v-alert
          v-if="device.message"
          :type="device.message.type"
          variant="tonal"
          density="compact"
          class="mt-4"
          data-test="login-message"
        >
          {{ device.message.text }}
        </v-alert>
      </v-card-text>
    </v-card>

    <v-dialog
      :model-value="device.warningOpen"
      max-width="480"
      persistent
    >
      <v-card>
        <v-card-title class="text-subtitle-1">
          最新値を取得
        </v-card-title>
        <v-card-text data-test="refresh-warning">
          {{ MESSAGES.refreshWarning }}
        </v-card-text>
        <v-card-actions>
          <v-spacer />
          <v-btn
            variant="text"
            data-test="cancel-refresh"
            @click="device.closeRefreshWarning()"
          >
            キャンセル
          </v-btn>
          <v-btn
            color="primary"
            variant="flat"
            data-test="confirm-refresh"
            @click="device.confirmRefresh()"
          >
            実行
          </v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </div>
</template>

<style scoped>
.assignment-row {
  cursor: pointer;
}
.assignment-row--active {
  background: rgba(var(--v-theme-primary), 0.08);
}
.login-fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(160px, 240px));
  gap: 12px;
}
.waiting {
  display: grid;
  gap: 8px;
  justify-items: start;
}
@media (max-width: 600px) {
  .login-fields {
    grid-template-columns: 1fr;
  }
}
</style>
