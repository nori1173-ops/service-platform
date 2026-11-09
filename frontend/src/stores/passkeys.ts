import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  associateWebAuthnCredential,
  deleteWebAuthnCredential,
  listWebAuthnCredentials,
} from 'aws-amplify/auth'
import { toJaMessage } from '@/utils/authErrors'

/** Cognito の 1 利用者あたりの上限 (変えられない。ADR D1) */
export const PASSKEY_LIMIT = 20

export type PasskeyAttachment = 'platform' | 'cross-platform'

/** 名前は Cognito が種類の名前を自動で付ける。利用者が付ける名前は持たない (判断 12) */
export interface PasskeyItem {
  credentialId: string
  name: string | null
  createdAt: Date | null
  attachment: PasskeyAttachment | null
}

function toAttachment(value: string | undefined): PasskeyAttachment | null {
  return value === 'platform' || value === 'cross-platform' ? value : null
}

/**
 * 自分のパスキーの一覧・追加・削除 (ADR D1 / D8 手順 1)。画面が Amplify で Cognito を直接呼ぶので、
 * SERVICE-PLATFORM の API は通らない (監査はアカウントの CloudTrail に残る)。
 */
export const usePasskeysStore = defineStore('passkeys', () => {
  const items = ref<PasskeyItem[]>([])
  const loaded = ref(false)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const atLimit = computed(() => items.value.length >= PASSKEY_LIMIT)

  async function readAll(): Promise<PasskeyItem[]> {
    const collected: PasskeyItem[] = []
    let nextToken: string | undefined
    do {
      const page = await listWebAuthnCredentials(nextToken ? { nextToken } : undefined)
      for (const credential of page.credentials) {
        if (credential.credentialId) {
          collected.push({
            credentialId: credential.credentialId,
            name: credential.friendlyCredentialName ?? null,
            createdAt: credential.createdAt ?? null,
            attachment: toAttachment(credential.authenticatorAttachment),
          })
        }
      }
      nextToken = page.nextToken
    } while (nextToken)
    return collected
  }

  async function fetch(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      items.value = await readAll()
      loaded.value = true
    } catch (e: unknown) {
      error.value = toJaMessage(e)
    } finally {
      loading.value = false
    }
  }

  async function change(action: () => Promise<void>): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      await action()
    } catch (e: unknown) {
      error.value = toJaMessage(e)
      loading.value = false
      return false
    }
    loading.value = false
    await fetch()
    return true
  }

  function add(): Promise<boolean> {
    return change(() => associateWebAuthnCredential())
  }

  function remove(credentialId: string): Promise<boolean> {
    return change(() => deleteWebAuthnCredential({ credentialId }))
  }

  function clear(): void {
    items.value = []
    loaded.value = false
    error.value = null
  }

  return { items, loaded, loading, error, atLimit, fetch, add, remove, clear }
})
