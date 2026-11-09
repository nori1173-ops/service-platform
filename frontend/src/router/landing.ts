import { START_LOCATION, type RouteLocationNormalized } from 'vue-router'
import { extractApiError } from '@/services/apiError'
import { baseApi } from '@/services/baseApi'
import { useDeviceStore } from '@/stores/device'
import type { MyDevice, ServiceLink } from '@/types/devices'

/**
 * 着地の近道 。admin でない利用者に見える通信機が 1 台で、その通信機のサービスが 1 つなら、
 * 入り方の選択と通信機の一覧を出さない。表示の出し分けだけで、権限の実体は各 API が台帳で確かめる (ADR D3)
 */
export type Landing =
  | { kind: 'start' }
  | { kind: 'login'; account: string }
  | { kind: 'launch'; account: string; url: string }

const START: Landing = { kind: 'start' }

const GUEST_ROUTE_NAMES: readonly unknown[] = ['login', 'login-admin']

/** 着地 = 読み込んだ直後の最初の画面か、サインインの直後。アプリの中でホームへ移ったときは近道しない (往復しない) */
export function isLanding(from: RouteLocationNormalized): boolean {
  return from === START_LOCATION || GUEST_ROUTE_NAMES.includes(from.name)
}

export function shortcutDevice(devices: readonly MyDevice[], isAdmin: boolean): MyDevice | null {
  const only = devices.length === 1 ? devices[0] : undefined
  return !isAdmin && only?.service_count === 1 ? only : null
}

/** 起動先は同じサイトの絶対パスだけ (API の値でも外のサイトへは移らない) */
function isSitePath(url: string): boolean {
  return url.startsWith('/') && !url.startsWith('//')
}

/** サービスが 1 つならその起動先。同じサービスの許可が 2 つあっても 1 つと数える */
export function launchTarget(services: readonly ServiceLink[]): string | null {
  const ids = new Set(services.map((service) => service.id))
  const url = services[0]?.url
  return ids.size === 1 && url !== undefined && isSitePath(url) ? url : null
}

/**
 * 着地の行き先。通信機ログインの記録が有効か (条件 (1)〜(3)) は、サービスの一覧の API の成否で見る。
 * 読めない・条件が崩れたときは今までの画面 (入り方の選択) に戻す
 */
export async function resolveLanding(isAdmin: boolean): Promise<Landing> {
  if (isAdmin) {
    return START
  }
  const device = useDeviceStore()
  await device.loadDevices()
  const only = device.loadError ? null : shortcutDevice(device.devices, false)
  if (!only) {
    return START
  }
  try {
    const url = launchTarget((await baseApi.getDeviceServices(only.account)).services)
    return url ? { kind: 'launch', account: only.account, url } : START
  } catch (e: unknown) {
    return extractApiError(e)?.code === 'DEVICE_LOGIN_REQUIRED' ? { kind: 'login', account: only.account } : START
  }
}
