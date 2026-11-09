import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { extractApiError } from '@/services/apiError'
import { baseApi } from '@/services/baseApi'
import type { DeviceLoginResult, DeviceServices, MyDevice } from '@/types/devices'
import { useMeStore } from './me'

// カメラ (地点登録補助) の装置設定の読取りと同じ 10 秒間隔・10 分 (カメラ設計書 §5.5)
export const POLL_INTERVAL_MS = 10_000
export const WAIT_LIMIT_MS = 10 * 60 * 1000
const SERIAL_LENGTH = 8
const PIN_LENGTH = 4

/** 文言はカメラの通信機ログイン (カメラ設計書 §5.5) を写し、土台で足した場面の文を加えた */
export const MESSAGES = Object.freeze({
  serialMismatch: '製造番号が違います',
  pinMismatch:
    '暗証番号が違うか、この通信機の設定がまだ読み取られていません (設置した当日など)。暗証番号を確かめても進めないときは、管理者へお問い合わせください',
  refreshWarning: '通信機と通信して設定を読み取ります。完了まで数分〜数十分かかることがあります。',
  waitCancelled:
    '画面上の待機を中断しました。中断してもコマンドは通信機側で完了します。完了後は前回値として参照できます。',
  waitTimedOut: '10 分を超えたため待機を中断しました。完了した結果は後から前回値として参照できます。',
  noResponse: '通信機から応答がありません。暗証番号と通信機の状態を確認してください。',
  inProgress: '実行中の取得があります。結果の待機を再開しました。',
  inProgressOther: 'ほかの方が最新値を取得しています。終わってから「前回値を読み込む」を押してください。',
  noPreviousSettings: '最新値の取得は、通信機が一度設定を報告した後に使えます。',
  userDenied: 'アカウントの状態が変わったため、通信機ログインを続けられません。アカウントの画面で状態を確かめてください。',
  guardUnavailable: '運用中かどうかを確認できないため、最新値の取得はできません。時間をおいて再度お試しください。',
  networkError: '通信に失敗しました。ネットワークの状態を確認して、もう一度お試しください。',
  device_backendError: '通信機の情報を取得できませんでした。時間をおいて再度お試しください。',
  waiting: '通信機からの報告を待っています',
  pinLocked: '暗証番号の誤りが続いたため、この通信機の照会を 10 分止めています。時間をおいてからやり直してください。',
  userPinLocked: '暗証番号の誤りが続いたため、通信機の照会を 60 分止めています。時間をおいてからやり直してください。',
  notAssigned: 'この通信機は割り当てられていません。管理者へお問い合わせください。',
  loginRequired: '通信機ログインの期限が切れたか、まだログインしていません。通信機ログインからやり直してください。',
  sessionExpired: 'サインインの期限が切れました。もう一度サインインしてください。',
  serialFormat: '製造番号は 8 桁の数字で入力してください。',
  pinFormat: '暗証番号は 4 桁の数字で入力してください。',
  loadFailed: '割り当てられた通信機を読めませんでした。しばらくしてから読み直してください。',
})

const FAILURE_TEXTS: Record<string, string> = {
  PIN_MISMATCH: MESSAGES.pinMismatch,
  SERIAL_MISMATCH: MESSAGES.serialMismatch,
  PIN_LOCKED: MESSAGES.pinLocked,
  USER_PIN_LOCKED: MESSAGES.userPinLocked,
  DEVICE_NOT_ASSIGNED: MESSAGES.notAssigned,
  NO_RESPONSE: MESSAGES.noResponse,
  GUARD_UNAVAILABLE: MESSAGES.guardUnavailable,
  DEVICE_BACKEND_UNAVAILABLE: MESSAGES.device_backendError,
  NO_PREVIOUS_SETTINGS: MESSAGES.noPreviousSettings,
}

/** 利用者の状態による拒否 (停止・再招待・admin の状態)。状態を読み直してアカウントの画面へ案内する */
const USER_DENIED_CODES: ReadonlySet<string> = new Set(['USER_SUSPENDED', 'USER_NOT_REGISTERED', 'ADMIN_NOT_ACTIVE'])

/** 待機中でも待機を終える理由 (それ以外の失敗は「報告がまだ」として待ち続ける) */
const WAIT_ENDING_CODES: ReadonlySet<string> = new Set(['SERIAL_MISMATCH', 'PIN_LOCKED', 'USER_PIN_LOCKED'])

export type DevicePhase = 'idle' | 'previous' | 'starting' | 'waiting'

export interface DeviceMessage {
  type: 'error' | 'warning' | 'info' | 'success'
  text: string
}

export interface Availability {
  account: string
  available: boolean
  reason: string | null
  inProgress: boolean
  loading: boolean
}

const FULL_WIDTH_DIGIT = /[０-９]/g

/** ASCII の数字だけにして桁数で切る。日本語入力の全角数字は半角に直す (カメラ設計書 §5.1) */
export function digitsOnly(value: unknown, maxLength: number): string {
  return String(value ?? '')
    .replace(FULL_WIDTH_DIGIT, (char) => String.fromCharCode(char.charCodeAt(0) - 0xfee0))
    .replace(/[^0-9]/g, '')
    .slice(0, maxLength)
}

function emptyAvailability(): Availability {
  return { account: '', available: false, reason: null, inProgress: false, loading: false }
}

function statusOf(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'response' in error) {
    return (error as { response?: { status?: number } }).response?.status
  }
  return undefined
}

function failureMessage(error: unknown): string {
  if (statusOf(error) === undefined) {
    return MESSAGES.networkError
  }
  const api = extractApiError(error)
  return (api?.code ? FAILURE_TEXTS[api.code] : undefined) ?? api?.message ?? MESSAGES.device_backendError
}

// 待機のタイマーと受付の poll_token はストアの外に持つ (状態に入れない。開発ツールにも出さない)
let pollTimer: ReturnType<typeof setTimeout> | null = null
let elapsedTimer: ReturnType<typeof setInterval> | null = null
let pollCredentials: { acceptance_id: string; poll_token: string } | null = null
// 待機を始める・画面を離れる・初期化するたびに増やす。前の世代で始めた照会の遅れた応答を捨てるため
let generation = 0

/**
 * 通信機ログイン (ADR D5、カメラ設計書 §5)。暗証番号はメモリだけに持ち、照会に使ったら捨てる。
 * 「最新値を取得」の待機中だけは再試行の照会に使うので持ち、成立・失敗・中断・打ち切り・画面を離れたときに捨てる。
 */
export const useDeviceStore = defineStore('device', () => {
  const devices = ref<MyDevice[]>([])
  const loading = ref(false)
  const loadError = ref<string | null>(null)
  const selected = ref<MyDevice | null>(null)
  const serial = ref('')
  const pin = ref('')
  const phase = ref<DevicePhase>('idle')
  const availability = ref<Availability>(emptyAvailability())
  const waitStartedAt = ref<number | null>(null)
  const elapsedSeconds = ref(0)
  const message = ref<DeviceMessage | null>(null)
  const warningOpen = ref(false)
  const loggedIn = ref<DeviceLoginResult | null>(null)
  /** 画面が移る先 (利用者の状態で断られたとき)。画面が 1 回だけ読む */
  const leaveTo = ref<'account' | null>(null)

  // /me/devices は今有効な割り当てと everyone の稼働中の通信機だけを返す (通信機アカウントで 1 行)
  const assignedDevices = computed(() => devices.value.filter((item) => item.basis !== 'everyone'))
  const everyoneDevices = computed(() => devices.value.filter((item) => item.basis === 'everyone'))
  const needsSerial = computed(() => selected.value !== null && !selected.value.serial)
  const errors = computed(() => {
    const result: { serial?: string; pin?: string } = {}
    if (!/^[0-9]{8}$/.test(serial.value)) {
      result.serial = MESSAGES.serialFormat
    }
    if (!/^[0-9]{4}$/.test(pin.value)) {
      result.pin = MESSAGES.pinFormat
    }
    return result
  })
  const busy = computed(() => phase.value !== 'idle')
  const canLoadPrevious = computed(
    () => selected.value !== null && Object.keys(errors.value).length === 0 && !busy.value,
  )
  const availabilityKnown = computed(
    () =>
      selected.value !== null &&
      availability.value.account === selected.value.account &&
      !availability.value.loading,
  )
  const canStartLatest = computed(
    () => canLoadPrevious.value && availabilityKnown.value && availability.value.available,
  )
  // 運用中で使えないときは理由の文を出さない。判定できなかったときだけ出す (カメラ設計書 §6.2)
  const refreshBlockedReason = computed(() =>
    availabilityKnown.value && availability.value.reason === 'guard_unavailable' ? MESSAGES.guardUnavailable : '',
  )

  function clearSelection(): void {
    selected.value = null
    serial.value = ''
    pin.value = ''
    availability.value = emptyAvailability()
  }

  /**
   * 取り直した一覧で、選んでいた通信機を根拠 (利用期間・種類・assignment_id) で照合する (製造番号の控えを反映する)。
   * 同じ通信機でも付け直した割り当てなら別のものとして選択を捨てる
   */
  function rematch(): void {
    const current = selected.value
    if (!current) {
      return
    }
    const found = devices.value.find(
      (item) =>
        item.account === current.account &&
        item.device_period_id === current.device_period_id &&
        item.basis === current.basis &&
        item.assignment_id === current.assignment_id,
    )
    if (!found) {
      if (!busy.value) {
        clearSelection()
      }
      return
    }
    if (found.serial && found.serial !== current.serial && !busy.value) {
      serial.value = found.serial
    }
    selected.value = found
  }

  async function loadDevices(): Promise<void> {
    const started = generation
    loading.value = true
    loadError.value = null
    try {
      const result = (await baseApi.getDevices()).devices
      // 初期化 (サインアウト) の前に始めた取得の応答は、次の利用者の画面に出さない
      if (started === generation) {
        devices.value = result
        rematch()
      }
    } catch (e: unknown) {
      if (started === generation) {
        devices.value = []
        loadError.value = extractApiError(e)?.message ?? MESSAGES.loadFailed
      }
    } finally {
      if (started === generation) {
        loading.value = false
      }
    }
  }

  function denied(code: string): void {
    const me = useMeStore()
    me.noteDenied(code)
    void me.load()
    leaveTo.value = 'account'
  }

  /** 失敗の案内。利用者の状態による拒否ならアカウントの画面へ、割り当ての終了なら一覧を読み直す */
  function failed(e: unknown): DeviceMessage {
    const code = extractApiError(e)?.code
    if (code && USER_DENIED_CODES.has(code)) {
      denied(code)
      return { type: 'error', text: MESSAGES.userDenied }
    }
    if (code === 'DEVICE_NOT_ASSIGNED') {
      void loadDevices()
    }
    return { type: 'error', text: failureMessage(e) }
  }

  // 最新値のガード (カメラ設計書 §5.5)。問い合わせに失敗したら押せない (fail-closed)
  async function checkAvailability(account: string): Promise<void> {
    availability.value = { ...emptyAvailability(), account, loading: true }
    try {
      const result = await baseApi.getRefreshAvailability(account)
      if (selected.value?.account !== account) {
        return
      }
      availability.value = {
        account,
        available: result.available === true,
        reason: result.reason ?? null,
        inProgress: result.in_progress === true,
        loading: false,
      }
    } catch {
      if (selected.value?.account !== account) {
        return
      }
      availability.value = { account, available: false, reason: 'guard_unavailable', inProgress: false, loading: false }
    }
  }

  async function select(account: string): Promise<void> {
    if (busy.value) {
      return
    }
    const found = devices.value.find((item) => item.account === account)
    if (!found) {
      return
    }
    selected.value = found
    serial.value = found.serial ?? ''
    pin.value = ''
    message.value = null
    loggedIn.value = null
    await checkAvailability(account)
  }

  /** 製造番号の控えが無いときだけ入力させる (控えのある割り当ては控えを使う) */
  function setSerial(value: unknown): void {
    if (busy.value || !needsSerial.value) {
      return
    }
    serial.value = digitsOnly(value, SERIAL_LENGTH)
  }

  function setPin(value: unknown): void {
    if (busy.value) {
      return
    }
    pin.value = digitsOnly(value, PIN_LENGTH)
  }

  function stopTimers(): void {
    if (pollTimer !== null) {
      clearTimeout(pollTimer)
    }
    if (elapsedTimer !== null) {
      clearInterval(elapsedTimer)
    }
    pollTimer = null
    elapsedTimer = null
    pollCredentials = null
  }

  function onSucceeded(result: DeviceLoginResult): void {
    stopTimers()
    pin.value = ''
    phase.value = 'idle'
    waitStartedAt.value = null
    message.value = null
    loggedIn.value = result
  }

  /**
   * 前回値の読み込みが 409 CONFLICT (同じ利用者の並行するログイン) で落ちたとき。一覧を読み直し、サービスの一覧の
   * API (条件 (1)〜(3)) で確かめる。先に書いた方でログイン済みならその一覧を返す (計画 4.12 のリスク)
   */
  async function servicesAfterConflict(account: string): Promise<DeviceServices | null> {
    await loadDevices()
    try {
      return await baseApi.getDeviceServices(account)
    } catch {
      return null
    }
  }

  // 前回値は通信機と通信しないので、待機の演出 (経過時間・中断) を出さない
  async function loadPrevious(): Promise<void> {
    const current = selected.value
    if (!canLoadPrevious.value || !current) {
      return
    }
    const input = { account: current.account, serial: serial.value, pin: pin.value, mode: 'previous' as const }
    const started = generation
    phase.value = 'previous'
    message.value = null
    try {
      const result = await baseApi.deviceLogin(input)
      if (started === generation) {
        onSucceeded(result)
      }
    } catch (e: unknown) {
      if (started !== generation) {
        return
      }
      pin.value = ''
      const confirmed =
        extractApiError(e)?.code === 'CONFLICT' ? await servicesAfterConflict(current.account) : null
      if (started !== generation) {
        return
      }
      if (confirmed) {
        // 画面はサービスの一覧へ移るのに account だけを使う。一覧は移った先で読み直す
        onSucceeded({
          account: confirmed.account,
          method: confirmed.method ?? 'previous',
          location_name: confirmed.location_name,
          observation: {},
          devices: [],
          services: confirmed.services,
        })
        return
      }
      message.value = failed(e)
    } finally {
      if (started === generation && phase.value === 'previous') {
        phase.value = 'idle'
      }
    }
  }

  function openRefreshWarning(): void {
    if (canStartLatest.value) {
      warningOpen.value = true
    }
  }

  function closeRefreshWarning(): void {
    warningOpen.value = false
  }

  function finishWaiting(next: DeviceMessage): void {
    stopTimers()
    phase.value = 'idle'
    pin.value = ''
    waitStartedAt.value = null
    message.value = next
  }

  function waitedTooLong(): boolean {
    return waitStartedAt.value !== null && Date.now() - waitStartedAt.value >= WAIT_LIMIT_MS
  }

  function schedulePoll(): void {
    pollTimer = setTimeout(() => void pollOnce(), POLL_INTERVAL_MS)
  }

  // 待機中の照会の失敗は「報告がまだ届いていない」と「暗証番号の誤り」を区別できないので待ち続ける (§5.5)
  async function pollOnce(): Promise<void> {
    pollTimer = null
    const current = selected.value
    if (phase.value !== 'waiting' || !current) {
      return
    }
    if (waitedTooLong()) {
      finishWaiting({ type: 'warning', text: MESSAGES.waitTimedOut })
      return
    }
    const started = generation
    const input = {
      account: current.account,
      serial: serial.value,
      pin: pin.value,
      mode: 'latest' as const,
      ...(pollCredentials ?? {}),
    }
    try {
      const result = await baseApi.deviceLogin(input)
      if (phase.value === 'waiting' && started === generation) {
        onSucceeded(result)
      }
      return
    } catch (e: unknown) {
      if (phase.value !== 'waiting' || started !== generation) {
        return
      }
      if (statusOf(e) === 401) {
        finishWaiting({ type: 'error', text: MESSAGES.sessionExpired })
        return
      }
      const code = extractApiError(e)?.code
      if (code && (USER_DENIED_CODES.has(code) || code === 'DEVICE_NOT_ASSIGNED')) {
        finishWaiting(failed(e))
        return
      }
      if (code && WAIT_ENDING_CODES.has(code)) {
        finishWaiting({ type: 'error', text: FAILURE_TEXTS[code] })
        return
      }
    }
    if (waitedTooLong()) {
      finishWaiting({ type: 'warning', text: MESSAGES.waitTimedOut })
      return
    }
    schedulePoll()
  }

  function beginWaiting(credentials: { acceptance_id: string; poll_token: string } | null): void {
    stopTimers()
    generation += 1
    pollCredentials = credentials
    phase.value = 'waiting'
    const startedAt = Date.now()
    waitStartedAt.value = startedAt
    elapsedSeconds.value = 0
    elapsedTimer = setInterval(() => {
      elapsedSeconds.value = Math.floor((Date.now() - startedAt) / 1000)
    }, 1000)
    schedulePoll()
  }

  async function confirmRefresh(): Promise<void> {
    warningOpen.value = false
    const current = selected.value
    if (!canStartLatest.value || !current) {
      return
    }
    const account = current.account
    const started = generation
    phase.value = 'starting'
    message.value = null
    try {
      const result = await baseApi.startRefresh({ account, serial: serial.value, pin: pin.value })
      if (started !== generation) {
        return
      }
      const credentials =
        result?.acceptance_id && result?.poll_token
          ? { acceptance_id: result.acceptance_id, poll_token: result.poll_token }
          : null
      beginWaiting(credentials)
      if (result?.resumed) {
        message.value = { type: 'info', text: MESSAGES.inProgress }
      }
    } catch (e: unknown) {
      if (started !== generation) {
        return
      }
      const code = extractApiError(e)?.code
      phase.value = 'idle'
      if (code === 'IN_PROGRESS') {
        message.value = { type: 'info', text: MESSAGES.inProgressOther }
        return
      }
      if (code === 'IN_OPERATION') {
        availability.value = { account, available: false, reason: 'in_operation', inProgress: false, loading: false }
        return
      }
      pin.value = ''
      message.value = failed(e)
    }
  }

  function cancelWaiting(): void {
    if (phase.value === 'waiting') {
      finishWaiting({ type: 'info', text: MESSAGES.waitCancelled })
    }
  }

  /** 画面を離れるとき。待機を止め、メモリ上の暗証番号を捨てる */
  function dispose(): void {
    stopTimers()
    generation += 1
    // 前の世代の取得は終わりを書かないので、読み込み中の表示もここで消す
    loading.value = false
    pin.value = ''
    phase.value = 'idle'
    waitStartedAt.value = null
    warningOpen.value = false
  }

  /** サインアウト・利用者の切り替えのとき。前の利用者の選択・入力・一覧・案内を全部捨てる */
  function reset(): void {
    dispose()
    devices.value = []
    loadError.value = null
    clearSelection()
    elapsedSeconds.value = 0
    message.value = null
    loggedIn.value = null
    leaveTo.value = null
  }

  /** 画面が移る先を 1 回だけ渡す */
  function consumeLeave(): 'account' | null {
    const target = leaveTo.value
    leaveTo.value = null
    return target
  }

  /** 成立したログインの結果を 1 回だけ渡す (画面がサービスの一覧へ移るため) */
  function consumeLogin(): DeviceLoginResult | null {
    const result = loggedIn.value
    loggedIn.value = null
    return result
  }

  return {
    devices,
    assignedDevices,
    everyoneDevices,
    loading,
    loadError,
    selected,
    serial,
    pin,
    phase,
    availability,
    elapsedSeconds,
    message,
    warningOpen,
    loggedIn,
    leaveTo,
    needsSerial,
    errors,
    busy,
    canLoadPrevious,
    canStartLatest,
    refreshBlockedReason,
    loadDevices,
    select,
    setSerial,
    setPin,
    loadPrevious,
    openRefreshWarning,
    closeRefreshWarning,
    confirmRefresh,
    cancelWaiting,
    dispose,
    reset,
    consumeLogin,
    consumeLeave,
  }
})
