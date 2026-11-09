import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import {
  associateWebAuthnCredential,
  deleteWebAuthnCredential,
  listWebAuthnCredentials,
} from 'aws-amplify/auth'
import { PASSKEY_LIMIT, usePasskeysStore } from './passkeys'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

type ListOutput = Awaited<ReturnType<typeof listWebAuthnCredentials>>

function credential(id: string, overrides: Record<string, unknown> = {}) {
  return {
    credentialId: id,
    friendlyCredentialName: 'Device-bound Passkey',
    relyingPartyId: 'service-platform-dev.example-cloud.com',
    authenticatorAttachment: 'platform',
    authenticatorTransports: ['internal'],
    createdAt: new Date('2026-11-04T01:02:03Z'),
    ...overrides,
  }
}

function namedError(name: string): Error {
  const error = new Error(name)
  error.name = name
  return error
}

describe('usePasskeysStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('fetch で一覧を名前・登録日時・認証器の種類に直す', async () => {
    vi.mocked(listWebAuthnCredentials).mockResolvedValue({
      credentials: [
        credential('cred-1'),
        credential('cred-2', {
          friendlyCredentialName: 'Roaming passkey',
          authenticatorAttachment: 'cross-platform',
        }),
        credential('cred-3', { authenticatorAttachment: undefined, createdAt: undefined }),
      ],
    } as unknown as ListOutput)
    const store = usePasskeysStore()

    await store.fetch()

    expect(store.items).toEqual([
      {
        credentialId: 'cred-1',
        name: 'Device-bound Passkey',
        createdAt: new Date('2026-11-04T01:02:03Z'),
        attachment: 'platform',
      },
      {
        credentialId: 'cred-2',
        name: 'Roaming passkey',
        createdAt: new Date('2026-11-04T01:02:03Z'),
        attachment: 'cross-platform',
      },
      { credentialId: 'cred-3', name: 'Device-bound Passkey', createdAt: null, attachment: null },
    ])
    expect(store.loaded).toBe(true)
    expect(store.atLimit).toBe(false)
  })

  it('次のページがあれば続けて読む', async () => {
    vi.mocked(listWebAuthnCredentials)
      .mockResolvedValueOnce({
        credentials: [credential('cred-1')],
        nextToken: 'next-1',
      } as unknown as ListOutput)
      .mockResolvedValueOnce({ credentials: [credential('cred-2')] } as unknown as ListOutput)
    const store = usePasskeysStore()

    await store.fetch()

    expect(listWebAuthnCredentials).toHaveBeenNthCalledWith(2, { nextToken: 'next-1' })
    expect(store.items.map((item) => item.credentialId)).toEqual(['cred-1', 'cred-2'])
  })

  it('credentialId の無い項目は消せないので一覧に出さない', async () => {
    vi.mocked(listWebAuthnCredentials).mockResolvedValue({
      credentials: [credential('cred-1'), credential('', { credentialId: undefined })],
    } as unknown as ListOutput)
    const store = usePasskeysStore()

    await store.fetch()

    expect(store.items).toHaveLength(1)
  })

  it(`${PASSKEY_LIMIT} 個で上限に達する`, async () => {
    vi.mocked(listWebAuthnCredentials).mockResolvedValue({
      credentials: Array.from({ length: PASSKEY_LIMIT }, (_, index) => credential(`cred-${index}`)),
    } as unknown as ListOutput)
    const store = usePasskeysStore()

    await store.fetch()

    expect(PASSKEY_LIMIT).toBe(20)
    expect(store.atLimit).toBe(true)
  })

  it('add は associateWebAuthnCredential を呼んで一覧を読み直す', async () => {
    vi.mocked(listWebAuthnCredentials).mockResolvedValue({
      credentials: [credential('cred-1')],
    } as unknown as ListOutput)
    const store = usePasskeysStore()

    expect(await store.add()).toBe(true)

    expect(associateWebAuthnCredential).toHaveBeenCalledTimes(1)
    expect(listWebAuthnCredentials).toHaveBeenCalledTimes(1)
    expect(store.items).toHaveLength(1)
  })

  it('add の取り消しは日本語の文言になり false を返す', async () => {
    vi.mocked(associateWebAuthnCredential).mockRejectedValue(
      namedError('PasskeyRegistrationCanceled'),
    )
    const store = usePasskeysStore()

    expect(await store.add()).toBe(false)

    expect(store.error).toContain('パスキーの登録が取り消されました')
    expect(listWebAuthnCredentials).not.toHaveBeenCalled()
  })

  it('同じ認証器への 2 つ目は PasskeyAlreadyExists の文言になる', async () => {
    vi.mocked(associateWebAuthnCredential).mockRejectedValue(namedError('PasskeyAlreadyExists'))
    const store = usePasskeysStore()

    expect(await store.add()).toBe(false)

    expect(store.error).toContain('既にパスキーが登録されています')
  })

  it('remove は deleteWebAuthnCredential に credentialId を渡して読み直す', async () => {
    vi.mocked(listWebAuthnCredentials).mockResolvedValue({ credentials: [] } as unknown as ListOutput)
    const store = usePasskeysStore()

    expect(await store.remove('cred-1')).toBe(true)

    expect(deleteWebAuthnCredential).toHaveBeenCalledWith({ credentialId: 'cred-1' })
    expect(listWebAuthnCredentials).toHaveBeenCalledTimes(1)
  })

  it('一覧が読めないと文言を持ち、loaded にならない', async () => {
    vi.mocked(listWebAuthnCredentials).mockRejectedValue(namedError('TooManyRequestsException'))
    const store = usePasskeysStore()

    await store.fetch()

    expect(store.loaded).toBe(false)
    expect(store.error).toContain('アクセスが集中しています')
  })

  it('clear で一覧を捨てる', async () => {
    vi.mocked(listWebAuthnCredentials).mockResolvedValue({
      credentials: [credential('cred-1')],
    } as unknown as ListOutput)
    const store = usePasskeysStore()
    await store.fetch()

    store.clear()

    expect(store.items).toEqual([])
    expect(store.loaded).toBe(false)
  })
})
